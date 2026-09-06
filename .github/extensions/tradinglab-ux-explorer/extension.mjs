import { joinSession } from "@github/copilot-sdk/extension";
import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { once } from "node:events";
import {
    access,
    appendFile,
    mkdir,
    readFile,
    realpath,
    rm,
    writeFile,
} from "node:fs/promises";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const extensionDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(extensionDir, "..", "..", "..");
const driverPath = join(repoRoot, "tools", "ux_explorer", "native_driver.ps1");
const desktopLockPath = join(repoRoot, "tools", "ux_explorer", "desktop_lock.ps1");
const artifactsRoot = join(repoRoot, "_ux_explorer");
const keyChordPattern =
    /^(?:(?:CTRL|ALT|SHIFT)\+){0,3}(?:[A-Z0-9,]|F(?:[1-9]|1[0-2])|BACKSPACE|TAB|ENTER|ESC|SPACE|COMMA|PAGEUP|PAGEDOWN|END|HOME|LEFT|UP|RIGHT|DOWN|DELETE)$/i;

let activeRun = null;
let desktopLockProcess = null;
let captureSequence = 0;
let lastCapture = [];
let operationQueue = Promise.resolve();

function serializeOperation(operation) {
    const next = operationQueue.then(operation);
    operationQueue = next.then(() => undefined, () => undefined);
    return next;
}

function isolatedEnvironment(profileDir, showSplash) {
    const allowed = new Set([
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
        "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOMEDRIVE", "HOMEPATH",
        "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
        "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS",
    ]);
    const env = Object.fromEntries(
        Object.entries(process.env).filter(([key]) => allowed.has(key.toUpperCase())),
    );
    env.TRADINGLAB_DATA_DIR = profileDir;
    env.TRADINGLAB_CACHE_DIR = profileDir;
    env.TRADINGLAB_TOKEN_DIR = join(profileDir, "tokens");
    env.TRADINGLAB_GEOMETRY_PATH = join(profileDir, "geometry.json");
    if (!showSplash) {
        env.TRADINGLAB_NO_SPLASH = "1";
        env.PYINSTALLER_SUPPRESS_SPLASH_SCREEN = "1";
    }
    return env;
}

function sleep(milliseconds) {
    return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

function nowIso() {
    return new Date().toISOString();
}

function safeSlug(value, fallback) {
    const cleaned = String(value || "")
        .trim()
        .replace(/[^A-Za-z0-9._-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 80);
    return cleaned || fallback;
}

async function pathExists(path) {
    try {
        await access(path);
        return true;
    } catch (error) {
        if (error.code === "ENOENT") return false;
        throw error;
    }
}

async function runDriver(parameters, timeout = 20_000) {
    const args = [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        driverPath,
    ];
    for (const [name, value] of Object.entries(parameters)) {
        if (value === undefined || value === null || value === "") {
            continue;
        }
        args.push(`-${name}`, String(value));
    }
    try {
        const { stdout } = await execFileAsync("pwsh.exe", args, {
            cwd: repoRoot,
            encoding: "utf8",
            maxBuffer: 20 * 1024 * 1024,
            timeout,
            windowsHide: true,
        });
        const text = stdout.trim();
        return text ? JSON.parse(text) : {};
    } catch (error) {
        const detail = String(error?.stderr || error?.message || error).trim();
        throw new Error(`TradingLab native driver failed: ${detail}`);
    }
}

async function appendJsonLine(path, value) {
    await appendFile(path, `${JSON.stringify(value)}\n`, "utf8");
}

async function trace(action, details = {}) {
    if (!activeRun) {
        return;
    }
    await appendJsonLine(activeRun.tracePath, {
        timestamp: nowIso(),
        action,
        ...details,
    });
}

async function acquireGlobalLock() {
    if (desktopLockProcess) {
        throw new Error("This session already owns the TradingLab UX desktop lock.");
    }
    const child = spawn(
        "pwsh.exe",
        [
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            desktopLockPath,
        ],
        {
            cwd: repoRoot,
            detached: false,
            stdio: ["pipe", "pipe", "pipe"],
            windowsHide: true,
        },
    );
    const exited = once(child, "exit");
    // Errors are also reported through the acquisition promise below.
    exited.catch(() => undefined);
    await new Promise((resolvePromise, rejectPromise) => {
        let stdout = "";
        let stderr = "";
        const timer = setTimeout(() => {
            child.stdin.end();
            rejectPromise(new Error("Timed out acquiring TradingLab UX desktop lock."));
        }, 5_000);
        const fail = (message) => {
            clearTimeout(timer);
            child.stdin.end();
            rejectPromise(new Error(message));
        };
        child.stdout.on("data", (chunk) => {
            stdout += chunk.toString("utf8");
            if (stdout.includes("LOCKED")) {
                clearTimeout(timer);
                resolvePromise();
            }
        });
        child.stderr.on("data", (chunk) => {
            stderr += chunk.toString("utf8");
        });
        child.once("error", (error) => fail(error.message));
        child.once("exit", (code) => {
            if (!stdout.includes("LOCKED")) {
                fail(
                    stderr.trim() ||
                    `TradingLab UX desktop lock exited with code ${code}.`,
                );
            }
        });
    });
    desktopLockProcess = { child, exited };
}

async function releaseGlobalLock() {
    const holder = desktopLockProcess;
    if (!holder) {
        return;
    }
    if (holder.child.exitCode === null) holder.child.stdin.end("\n");
    const [code] = await holder.exited;
    desktopLockProcess = null;
    if (code !== 0) throw new Error(`Desktop mutex helper exited with code ${code}.`);
}

async function resolveExecutable(requestedPath) {
    const expectedPath = join(repoRoot, "dist", "TradingLab", "TradingLab.exe");
    if (!await pathExists(expectedPath)) {
        throw new Error(
            "This checkout has no frozen build. Build dist\\TradingLab\\TradingLab.exe first.",
        );
    }
    const expected = await realpath(expectedPath);
    const canonical = await realpath(resolve(requestedPath || expected));
    if (
        basename(canonical).toLowerCase() !== "tradinglab.exe" ||
        canonical.toLowerCase() !== expected.toLowerCase()
    ) {
        throw new Error(
            "The UX explorer may launch only this checkout's " +
            "dist\\TradingLab\\TradingLab.exe.",
        );
    }
    return canonical;
}

async function findRunningExecutable(executablePath, runMarker = "") {
    const result = await runDriver({
        Action: "find",
        ExecutablePath: executablePath,
        RunMarker: runMarker,
    });
    return Array.isArray(result.matches) ? result.matches : [];
}

async function waitForWindow(executablePath, runMarker, child, timeoutMs = 30_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const matches = await findRunningExecutable(executablePath, runMarker);
        const ready = matches.find(
            (item) => Array.isArray(item.windows) &&
                item.windows.some((window) => window.title.startsWith("TradingLab v")),
        );
        if (ready) {
            return ready;
        }
        if (child.exitCode !== null) {
            throw new Error(`TradingLab exited during startup (code ${child.exitCode}).`);
        }
        await sleep(300);
    }
    throw new Error(
        "TradingLab did not expose a visible window within 30 seconds. " +
        "A pre-existing source build may own the single-instance mutex.",
    );
}

async function cleanupFailedLaunch(
    executablePath,
    runMarker,
) {
    let matches = [];
    try {
        matches = await findRunningExecutable(executablePath, runMarker);
        for (const match of matches) {
            if (Array.isArray(match.windows) && match.windows.length > 0) {
                await runDriver({
                    Action: "close",
                    TargetProcessId: Number(match.process_id),
                    ExpectedExecutablePath: executablePath,
                    RunMarker: runMarker,
                });
            }
        }
        const deadline = Date.now() + 5_000;
        while (Date.now() < deadline) {
            matches = await findRunningExecutable(executablePath, runMarker);
            if (matches.length === 0) {
                break;
            }
            await sleep(250);
        }
    } catch (error) {
        throw new Error(`Could not confirm failed-launch cleanup; mutex retained: ${error.message}`);
    }
    const remaining = matches[0];
    const remainingProcessId = remaining ? Number(remaining.process_id) : 0;
    if (remainingProcessId) {
        return false;
    }
    await releaseGlobalLock();
    return true;
}

async function activeRunIdentityExists(run) {
    const matches = await findRunningExecutable(run.executablePath, run.runMarker);
    return matches.some((match) => Number(match.process_id) === run.processId);
}

async function requireActiveRun() {
    if (!activeRun) {
        throw new Error("No TradingLab UX run is active. Call tradinglab_ux_start first.");
    }
    if (!desktopLockProcess || desktopLockProcess.child.exitCode !== null) {
        throw new Error("Desktop mutex ownership was lost; no further GUI input is allowed.");
    }
    if (!await activeRunIdentityExists(activeRun)) {
        const exitedRunId = activeRun.runId;
        await closeActiveRun("unexpected-exit");
        throw new Error(`TradingLab UX run ${exitedRunId} is no longer running.`);
    }
    return activeRun;
}

function clampSettle(value, fallback = 350) {
    const number = Number(value ?? fallback);
    if (!Number.isFinite(number)) {
        return fallback;
    }
    return Math.max(0, Math.min(5_000, Math.round(number)));
}

async function capture(waitMs = 0) {
    const run = await requireActiveRun();
    if (waitMs > 0) {
        await sleep(clampSettle(waitMs, 0));
    }
    captureSequence += 1;
    const captureId = String(captureSequence).padStart(4, "0");
    const outputDirectory = join(run.screenshotsDir, captureId);
    const result = await runDriver({
        Action: "capture",
        TargetProcessId: run.processId,
        ExpectedExecutablePath: run.executablePath,
        RunMarker: run.runMarker,
        OutputDirectory: outputDirectory,
        MaxImageWidth: 1400,
    });
    const windows = Array.isArray(result.windows) ? result.windows : [];
    if (!windows.length) {
        throw new Error("TradingLab produced no capturable windows.");
    }
    lastCapture = windows.map((window) => ({
        ...window,
        relative_path: relative(run.runDir, window.path),
    }));
    await trace("observe", {
        capture_id: captureId,
        windows: lastCapture.map((window) => ({
            id: window.id,
            title: window.title,
            class_name: window.class_name,
            width: window.width,
            height: window.height,
            relative_path: window.relative_path,
        })),
    });

    const binaryResultsForLlm = [];
    for (const window of windows) {
        binaryResultsForLlm.push({
            type: "image",
            mimeType: "image/png",
            data: (await readFile(window.path)).toString("base64"),
            description:
                `TradingLab window ${window.id} ${JSON.stringify(window.title)}. ` +
                "Click coordinates are normalized from 0 to 1000 within this image.",
        });
    }
    const descriptions = windows.map(
        (window) =>
            `- ${window.id} ${JSON.stringify(window.title)} ` +
            `${window.width}x${window.height}` +
            `${window.foreground ? " (foreground)" : ""}`,
    );
    return {
        textResultForLlm:
            `Captured TradingLab run ${run.runId}. ` +
            `Artifacts: ${run.runDir}\n` +
            `Input transport: ${run.inputMode}. ` +
            (run.inputMode === "foreground"
                ? "Input stops unless the selected TradingLab window is foreground. "
                : "Limited message simulation: failed menu/button activation is not an app defect. ") +
            "Use x/y values from 0..1000 relative to the selected window.\n" +
            descriptions.join("\n"),
        binaryResultsForLlm,
        resultType: "success",
    };
}

async function actAndCapture(action, driverParameters, traceDetails, settleMs) {
    const run = await requireActiveRun();
    if (run.actionCount >= 400 || Date.now() - Date.parse(run.startedAt) > 45 * 60_000) {
        throw new Error("Exploration budget reached (400 actions / 45 minutes). Observe and finish this run.");
    }
    run.actionCount += 1;
    const result = await runDriver({
        Action: action,
        TargetProcessId: run.processId,
        ExpectedExecutablePath: run.executablePath,
        RunMarker: run.runMarker,
        InputMode: run.inputMode,
        ...driverParameters,
    });
    await trace(action, {
        ...traceDetails,
        driver_result: result,
    });
    return capture(clampSettle(settleMs));
}

async function closeActiveRun(reason) {
    if (!activeRun) {
        return { closed: true, already_inactive: true };
    }
    let closeError = null;
    try {
        if (await activeRunIdentityExists(activeRun)) {
            await runDriver({
                Action: "close",
                TargetProcessId: activeRun.processId,
                ExpectedExecutablePath: activeRun.executablePath,
                RunMarker: activeRun.runMarker,
            });
            const deadline = Date.now() + 12_000;
            while (
                Date.now() < deadline &&
                await activeRunIdentityExists(activeRun)
            ) {
                await sleep(250);
            }
        }
    } catch (error) {
        closeError = String(error?.message || error);
    }
    let stopped = false;
    try {
        stopped = !await activeRunIdentityExists(activeRun);
    } catch (error) {
        closeError = String(error.message || error);
    }
    await trace("finish", {
        reason,
        stopped,
        close_error: closeError,
    });
    await writeFile(
        activeRun.summaryPath,
        JSON.stringify({
            schema_version: 1,
            run_id: activeRun.runId,
            scenario_id: activeRun.scenarioId,
            campaign_id: activeRun.campaignId,
            started_at: activeRun.startedAt,
            finished_at: stopped ? nowIso() : null,
            stopped,
            close_error: closeError,
            findings_path: activeRun.findingsPath,
            trace_path: activeRun.tracePath,
        }, null, 2),
        "utf8",
    );
    const completed = {
        run_id: activeRun.runId,
        run_dir: activeRun.runDir,
        stopped,
        close_error: closeError,
        lock_retained: !stopped,
    };
    if (stopped) {
        await releaseGlobalLock();
        activeRun = null;
        lastCapture = [];
        captureSequence = 0;
    }
    if (!stopped) {
        completed.next_action = "Observe and dismiss any confirmation dialog, then call finish again.";
    }
    return completed;
}

const session = await joinSession({
    tools: [
        {
            name: "tradinglab_ux_start",
            description:
                "Start an isolated exploratory run of the frozen TradingLab.exe. " +
                "This is the only tool that launches a process; it accepts only a verified " +
                "TradingLab.exe and refuses to run while another UX explorer owns the desktop.",
            parameters: {
                type: "object",
                properties: {
                    exe_path: {
                        type: "string",
                        description:
                            "Absolute path to dist\\TradingLab\\TradingLab.exe. " +
                            "Defaults to this worktree's build.",
                    },
                    scenario_id: {
                        type: "string",
                        description: "Stable scenario ID assigned by the campaign harness.",
                    },
                    campaign_id: {
                        type: "string",
                        description: "Campaign ID assigned by the campaign harness.",
                    },
                    notes: {
                        type: "string",
                        description: "Short non-sensitive run context.",
                    },
                    show_splash: {
                        type: "boolean",
                        description:
                            "Allow the frozen bootloader splash for startup exploration. " +
                            "Defaults to false.",
                    },
                    input_mode: {
                        type: "string",
                        enum: ["foreground", "messages"],
                        default: "foreground",
                        description: "Foreground uses real input on an exclusive unlocked desktop. Messages is limited simulation.",
                    },
                },
            },
            skipPermission: true,
            handler: async (args) => {
                if (process.platform !== "win32") {
                    throw new Error("TradingLab UX exploration requires Windows.");
                }
                if (activeRun) {
                    throw new Error(`UX run ${activeRun.runId} is already active.`);
                }
                const executablePath = await resolveExecutable(args.exe_path);
                const validation = await runDriver({
                    Action: "validate",
                    ExecutablePath: executablePath,
                });
                const existing = await findRunningExecutable(executablePath);
                if (existing.length) {
                    throw new Error(
                        "A TradingLab build is already running in this desktop session. " +
                        "Close it before starting an isolated exploration run.",
                    );
                }

                await acquireGlobalLock();
                const timestamp = nowIso().replace(/[:.]/g, "-");
                const scenarioId = safeSlug(args.scenario_id, "unassigned");
                const runId = `${timestamp}-${scenarioId}-${randomUUID().slice(0, 8)}`;
                const runMarker = randomUUID();
                const runDir = join(artifactsRoot, runId);
                const profileDir = join(runDir, "profile");
                const screenshotsDir = join(runDir, "screenshots");
                try {
                    await mkdir(profileDir, { recursive: true });
                    await mkdir(screenshotsDir, { recursive: true });
                    const child = spawn(
                        executablePath,
                        [`--ux-run-id=${runMarker}`],
                        {
                            cwd: profileDir,
                            env: isolatedEnvironment(profileDir, Boolean(args.show_splash)),
                            detached: false,
                            stdio: "ignore",
                            windowsHide: false,
                        },
                    );
                    await once(child, "spawn");
                    const found = await waitForWindow(executablePath, runMarker, child);
                    activeRun = {
                        runId,
                        campaignId: safeSlug(args.campaign_id, "ad-hoc"),
                        scenarioId,
                        executablePath,
                        processId: Number(found.process_id),
                        runMarker,
                        runDir,
                        profileDir,
                        screenshotsDir,
                        tracePath: join(runDir, "trace.jsonl"),
                        findingsPath: join(runDir, "findings.jsonl"),
                        summaryPath: join(runDir, "summary.json"),
                        startedAt: nowIso(),
                        actionCount: 0,
                        inputMode: args.input_mode || "foreground",
                    };
                    await writeFile(
                        join(runDir, "manifest.json"),
                        JSON.stringify({
                            schema_version: 1,
                            run_id: runId,
                            campaign_id: activeRun.campaignId,
                            scenario_id: scenarioId,
                            started_at: activeRun.startedAt,
                            executable: validation,
                            process_id: activeRun.processId,
                            run_marker: runMarker,
                            profile_dir: profileDir,
                            show_splash: Boolean(args.show_splash),
                            input_transport: activeRun.inputMode,
                            notes: String(args.notes || ""),
                        }, null, 2),
                        "utf8",
                    );
                    await trace("start", {
                        executable: validation,
                        process_id: activeRun.processId,
                    });
                    if (activeRun.inputMode === "foreground") {
                        const focus = await runDriver({
                            Action: "activate",
                            TargetProcessId: activeRun.processId,
                            ExpectedExecutablePath: executablePath,
                            RunMarker: runMarker,
                            WindowId: found.windows.find((window) => window.title.startsWith("TradingLab v")).id,
                        });
                        await trace("initial_focus", focus);
                    }
                    return await capture(800);
                } catch (error) {
                    let cleaned = false;
                    if (activeRun?.runMarker === runMarker) {
                        const result = await closeActiveRun("start-failed");
                        cleaned = Boolean(result.stopped);
                    } else {
                        cleaned = await cleanupFailedLaunch(
                            executablePath,
                            runMarker,
                        );
                    }
                    if (!cleaned) {
                        throw new Error(
                            `${String(error?.message || error)} ` +
                            "The TradingLab process did not close; the desktop lock " +
                            "was retained until it exits.",
                        );
                    }
                    throw error;
                }
            },
        },
        {
            name: "tradinglab_ux_observe",
            description:
                "Capture only the visible windows owned by the active TradingLab process. " +
                "Returns images plus stable window IDs; it never captures the full desktop.",
            parameters: {
                type: "object",
                properties: {
                    wait_ms: {
                        type: "integer",
                        minimum: 0,
                        maximum: 5000,
                        description: "Optional delay before capture.",
                    },
                },
            },
            skipPermission: true,
            handler: async (args) => capture(clampSettle(args.wait_ms, 0)),
        },
        {
            name: "tradinglab_ux_inspect",
            description:
                "Inspect Win32 windows and child controls owned by the active TradingLab process. " +
                "Use screenshots as the source of visual truth because Tk controls may not expose " +
                "complete accessibility metadata.",
            parameters: { type: "object", properties: {} },
            skipPermission: true,
            handler: async () => {
                const run = await requireActiveRun();
                const result = await runDriver({
                    Action: "inspect",
                    TargetProcessId: run.processId,
                    ExpectedExecutablePath: run.executablePath,
                    RunMarker: run.runMarker,
                });
                await trace("inspect", {
                    window_count: Array.isArray(result.windows) ? result.windows.length : 0,
                });
                return JSON.stringify(result, null, 2);
            },
        },
        {
            name: "tradinglab_ux_click",
            description:
                "Click within a TradingLab-owned window using normalized image coordinates. " +
                "Call observe first, choose its window_id, and use x/y in the range 0..1000.",
            parameters: {
                type: "object",
                properties: {
                    window_id: { type: "string", description: "Window ID from observe." },
                    x: { type: "integer", minimum: 0, maximum: 1000 },
                    y: { type: "integer", minimum: 0, maximum: 1000 },
                    button: {
                        type: "string",
                        enum: ["left", "right", "double"],
                        default: "left",
                    },
                    settle_ms: {
                        type: "integer",
                        minimum: 0,
                        maximum: 5000,
                        default: 350,
                    },
                },
                required: ["x", "y"],
            },
            skipPermission: true,
            handler: async (args) =>
                actAndCapture(
                    "click",
                    {
                        WindowId: args.window_id,
                        NormalizedX: args.x,
                        NormalizedY: args.y,
                        MouseButton: args.button || "left",
                    },
                    {
                        window_id: args.window_id || null,
                        normalized_x: args.x,
                        normalized_y: args.y,
                        button: args.button || "left",
                    },
                    args.settle_ms,
                ),
        },
        {
            name: "tradinglab_ux_type",
            description:
                "Type Unicode text into the focused control of a TradingLab-owned window. " +
                "Never enter real credentials; each run uses an isolated empty profile.",
            parameters: {
                type: "object",
                properties: {
                    window_id: { type: "string", description: "Window ID from observe." },
                    text: {
                        type: "string",
                        maxLength: 4000,
                        description: "Non-sensitive text to type.",
                    },
                    settle_ms: {
                        type: "integer",
                        minimum: 0,
                        maximum: 5000,
                        default: 350,
                    },
                },
                required: ["text"],
            },
            skipPermission: true,
            handler: async (args) => {
                const run = await requireActiveRun();
                const textPath = join(run.runDir, `input-${randomUUID()}.txt`);
                await writeFile(textPath, String(args.text), "utf8");
                try {
                    return await actAndCapture(
                        "type",
                        {
                            WindowId: args.window_id,
                            TextFile: textPath,
                        },
                        {
                            window_id: args.window_id || null,
                            character_count: String(args.text).length,
                        },
                        args.settle_ms,
                    );
                } finally {
                    await rm(textPath, { force: true });
                }
            },
        },
        {
            name: "tradinglab_ux_key",
            description:
                "Send a key or application shortcut to the selected foreground TradingLab window. " +
                "System-switching shortcuts are rejected. Message mode supports only unmodified keys.",
            parameters: {
                type: "object",
                properties: {
                    window_id: { type: "string", description: "Window ID from observe." },
                    chord: {
                        type: "string",
                        description: "Key or application shortcut, e.g. TAB, ENTER, CTRL+S. No modifiers in message mode.",
                    },
                    settle_ms: {
                        type: "integer",
                        minimum: 0,
                        maximum: 5000,
                        default: 350,
                    },
                },
                required: ["chord"],
            },
            skipPermission: true,
            handler: async (args) => {
                const chord = String(args.chord || "").toUpperCase();
                if (!keyChordPattern.test(chord)) {
                    throw new Error(`Unsupported key chord: ${chord}`);
                }
                const run = await requireActiveRun();
                if (run.inputMode === "messages" && chord.includes("+")) {
                    throw new Error("Modifier chords cannot be simulated faithfully by message mode. Use visible controls.");
                }
                return actAndCapture(
                    "key",
                    {
                        WindowId: args.window_id,
                        KeyChord: chord,
                    },
                    {
                        window_id: args.window_id || null,
                        key_chord: chord,
                    },
                    args.settle_ms,
                );
            },
        },
        {
            name: "tradinglab_ux_scroll",
            description:
                "Scroll within a TradingLab-owned window at normalized coordinates. " +
                "Positive clicks scroll up; negative clicks scroll down.",
            parameters: {
                type: "object",
                properties: {
                    window_id: { type: "string", description: "Window ID from observe." },
                    x: { type: "integer", minimum: 0, maximum: 1000 },
                    y: { type: "integer", minimum: 0, maximum: 1000 },
                    clicks: { type: "integer", minimum: -20, maximum: 20 },
                    settle_ms: {
                        type: "integer",
                        minimum: 0,
                        maximum: 5000,
                        default: 350,
                    },
                },
                required: ["x", "y", "clicks"],
            },
            skipPermission: true,
            handler: async (args) => {
                if (Number(args.clicks) === 0) {
                    throw new Error("clicks must be non-zero.");
                }
                return actAndCapture(
                    "scroll",
                    {
                        WindowId: args.window_id,
                        NormalizedX: args.x,
                        NormalizedY: args.y,
                        ScrollClicks: args.clicks,
                    },
                    {
                        window_id: args.window_id || null,
                        normalized_x: args.x,
                        normalized_y: args.y,
                        scroll_clicks: args.clicks,
                    },
                    args.settle_ms,
                );
            },
        },
        {
            name: "tradinglab_ux_window",
            description:
                "Resize, maximize, minimize, or restore a TradingLab-owned window, " +
                "then return updated screenshots. Use this to probe responsive layout.",
            parameters: {
                type: "object",
                properties: {
                    window_id: { type: "string", description: "Window ID from observe." },
                    operation: {
                        type: "string",
                        enum: ["restore", "maximize", "minimize", "resize"],
                    },
                    width: {
                        type: "integer",
                        minimum: 640,
                        maximum: 3840,
                        default: 1280,
                    },
                    height: {
                        type: "integer",
                        minimum: 480,
                        maximum: 2160,
                        default: 800,
                    },
                    settle_ms: {
                        type: "integer",
                        minimum: 0,
                        maximum: 5000,
                        default: 500,
                    },
                },
                required: ["operation"],
            },
            skipPermission: true,
            handler: async (args) =>
                actAndCapture(
                    "window",
                    {
                        WindowId: args.window_id,
                        WindowOperation: args.operation,
                        WindowWidth: args.width || 1280,
                        WindowHeight: args.height || 800,
                    },
                    {
                        window_id: args.window_id || null,
                        operation: args.operation,
                        width: args.width || 1280,
                        height: args.height || 800,
                    },
                    args.settle_ms,
                ),
        },
        {
            name: "tradinglab_ux_pointer",
            description:
                "Move or drag within the selected TradingLab window using normalized coordinates. " +
                "Foreground mode uses the real pointer; message mode cannot cover all cursor-dependent behavior.",
            parameters: {
                type: "object",
                properties: {
                    window_id: { type: "string" },
                    operation: { type: "string", enum: ["move", "drag"] },
                    x: { type: "integer", minimum: 0, maximum: 1000 },
                    y: { type: "integer", minimum: 0, maximum: 1000 },
                    end_x: { type: "integer", minimum: 0, maximum: 1000 },
                    end_y: { type: "integer", minimum: 0, maximum: 1000 },
                },
                required: ["window_id", "operation", "x", "y"],
            },
            skipPermission: true,
            handler: async (args) => {
                if (args.operation === "drag" && (args.end_x === undefined || args.end_y === undefined)) {
                    throw new Error("Drag requires end_x and end_y.");
                }
                return actAndCapture("pointer", {
                    WindowId: args.window_id,
                    PointerOperation: args.operation,
                    NormalizedX: args.x,
                    NormalizedY: args.y,
                    EndX: args.end_x ?? args.x,
                    EndY: args.end_y ?? args.y,
                }, args, 350);
            },
        },
        {
            name: "tradinglab_ux_record_finding",
            description:
                "Record a reproducible GUI/UX finding with the latest TradingLab screenshots " +
                "as evidence. Do this before changing source code.",
            parameters: {
                type: "object",
                properties: {
                    surface_id: { type: "string" },
                    severity: {
                        type: "string",
                        enum: ["blocker", "major", "minor", "polish"],
                    },
                    title: { type: "string" },
                    expected: { type: "string" },
                    observed: { type: "string" },
                    reproduction_steps: {
                        type: "array",
                        items: { type: "string" },
                        minItems: 1,
                    },
                },
                required: [
                    "surface_id",
                    "severity",
                    "title",
                    "expected",
                    "observed",
                    "reproduction_steps",
                ],
            },
            skipPermission: true,
            handler: async (args) => {
                const run = await requireActiveRun();
                if (lastCapture.length === 0) throw new Error("Observe before recording a finding.");
                const finding = {
                    schema_version: 1,
                    finding_id: randomUUID(),
                    timestamp: nowIso(),
                    run_id: run.runId,
                    campaign_id: run.campaignId,
                    scenario_id: run.scenarioId,
                    surface_id: safeSlug(args.surface_id, "unknown"),
                    severity: args.severity,
                    title: String(args.title),
                    expected: String(args.expected),
                    observed: String(args.observed),
                    reproduction_steps: args.reproduction_steps.map(String),
                    evidence: lastCapture.map((window) => ({
                        window_id: window.id,
                        title: window.title,
                        screenshot: window.relative_path,
                    })),
                };
                await appendJsonLine(run.findingsPath, finding);
                await trace("record_finding", {
                    finding_id: finding.finding_id,
                    surface_id: finding.surface_id,
                    severity: finding.severity,
                    title: finding.title,
                });
                return JSON.stringify(finding, null, 2);
            },
        },
        {
            name: "tradinglab_ux_status",
            description:
                "Return the active TradingLab UX run, process, artifact paths, and current windows.",
            parameters: { type: "object", properties: {} },
            skipPermission: true,
            handler: async () => {
                const run = await requireActiveRun();
                const inspection = await runDriver({
                    Action: "inspect",
                    TargetProcessId: run.processId,
                    ExpectedExecutablePath: run.executablePath,
                    RunMarker: run.runMarker,
                });
                return JSON.stringify({
                    run_id: run.runId,
                    campaign_id: run.campaignId,
                    scenario_id: run.scenarioId,
                    process_id: run.processId,
                    executable_path: run.executablePath,
                    run_dir: run.runDir,
                    input_mode: run.inputMode,
                    windows: inspection.windows,
                }, null, 2);
            },
        },
        {
            name: "tradinglab_ux_finish",
            description:
                "Close the active TradingLab run through WM_CLOSE and finalize its trace. " +
                "This never force-kills the process.",
            parameters: {
                type: "object",
                properties: {
                    notes: {
                        type: "string",
                        description: "Short non-sensitive completion note.",
                    },
                },
            },
            skipPermission: true,
            handler: async (args) => {
                if (activeRun) {
                    await trace("completion_notes", { notes: String(args.notes || "") });
                }
                return JSON.stringify(
                    await closeActiveRun("agent-finished"),
                    null,
                    2,
                );
            },
        },
    ].map((tool) => ({
        ...tool,
        handler: (args) => serializeOperation(async () => {
            try {
                return await tool.handler(args);
            } catch (error) {
                const message = String(error.message || error);
                await trace("tool_error", { tool: tool.name, message });
                return { textResultForLlm: message, resultType: "failure" };
            }
        }),
    })),
    hooks: {
        onSessionEnd: async (input, invocation) => {
            // This hook also fires for agent handoffs. It must not tear down
            // a desktop run another agent is still exploring.
            await trace("agent_session_end", {
                reason: input.reason,
                session_id: invocation?.sessionId,
            });
        },
    },
});

let shuttingDown = false;
function shutdown() {
    if (shuttingDown) return;
    shuttingDown = true;
    serializeOperation(async () => {
        if (activeRun) await closeActiveRun("extension-shutdown");
        else await releaseGlobalLock();
    }).then(
        () => process.exit(0),
        (error) => {
            process.stderr.write(`TradingLab UX shutdown: ${error.message}\n`);
            process.exit(1);
        },
    );
}
process.once("SIGTERM", shutdown);
process.once("SIGINT", shutdown);

await session.log(
    "TradingLab UX explorer loaded. It remains inert until tradinglab_ux_start is called.",
    { ephemeral: true },
);
