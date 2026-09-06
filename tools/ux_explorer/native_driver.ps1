[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("validate", "find", "activate", "inspect", "capture", "click", "type", "key", "scroll", "pointer", "window", "close")]
    [string]$Action,
    [int]$TargetProcessId = 0,
    [string]$ExecutablePath = "",
    [string]$ExpectedExecutablePath = "",
    [string]$RunMarker = "",
    [ValidateSet("foreground", "messages")]
    [string]$InputMode = "foreground",
    [string]$OutputDirectory = "",
    [string]$WindowId = "",
    [ValidateRange(0, 1000)]
    [int]$NormalizedX = 500,
    [ValidateRange(0, 1000)]
    [int]$NormalizedY = 500,
    [ValidateRange(0, 1000)]
    [int]$EndX = 500,
    [ValidateRange(0, 1000)]
    [int]$EndY = 500,
    [ValidateSet("move", "drag")]
    [string]$PointerOperation = "move",
    [ValidateSet("left", "right", "double")]
    [string]$MouseButton = "left",
    [string]$TextFile = "",
    [string]$KeyChord = "",
    [ValidateRange(-20, 20)]
    [int]$ScrollClicks = 0,
    [ValidateSet("restore", "maximize", "minimize", "resize")]
    [string]$WindowOperation = "restore",
    [ValidateRange(640, 3840)]
    [int]$WindowWidth = 1280,
    [ValidateRange(480, 2160)]
    [int]$WindowHeight = 800,
    [ValidateRange(320, 2400)]
    [int]$MaxImageWidth = 1400
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$TrustedRepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

if (-not ("TradingLabUxNative" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public sealed class TradingLabUxWindow {
    public long Hwnd { get; set; }
    public int ProcessId { get; set; }
    public string Title { get; set; } = "";
    public string ClassName { get; set; } = "";
    public int Left { get; set; }
    public int Top { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
    public bool Foreground { get; set; }
    public bool Enabled { get; set; }
}

public static class TradingLabUxNative {
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MINMAXINFO {
        public POINT Reserved;
        public POINT MaxSize;
        public POINT MaxPosition;
        public POINT MinTrackSize;
        public POINT MaxTrackSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct GUITHREADINFO {
        public int cbSize;
        public int flags;
        public IntPtr hwndActive;
        public IntPtr hwndFocus;
        public IntPtr hwndCapture;
        public IntPtr hwndMenuOwner;
        public IntPtr hwndMoveSize;
        public IntPtr hwndCaret;
        public RECT rcCaret;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT {
        public uint type;
        public INPUTUNION data;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct INPUTUNION {
        [FieldOffset(0)] public MOUSEINPUT mouse;
        [FieldOffset(0)] public KEYBDINPUT keyboard;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT {
        public int dx;
        public int dy;
        public uint mouseData;
        public uint flags;
        public uint time;
        public UIntPtr extraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KEYBDINPUT {
        public ushort key;
        public ushort scan;
        public uint flags;
        public uint time;
        public UIntPtr extraInfo;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, INPUT[] inputs, int size);

    [DllImport("user32.dll")]
    private static extern IntPtr WindowFromPoint(POINT point);

    [DllImport("user32.dll")]
    private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern void SwitchToThisWindow(IntPtr hwnd, bool altTab);

    [DllImport("user32.dll")]
    private static extern bool AttachThreadInput(uint first, uint second, bool attach);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    [DllImport("user32.dll")]
    private static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int key);

    [DllImport("user32.dll")]
    private static extern uint MapVirtualKey(uint code, uint mapType);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(
        IntPtr parent, EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(
        IntPtr hwnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool IsWindowEnabled(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern int GetWindowTextLength(IntPtr hwnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(
        IntPtr hwnd, StringBuilder text, int maxCount);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(
        IntPtr hwnd, StringBuilder className, int maxCount);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hwnd, int command);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool MoveWindow(
        IntPtr hwnd, int x, int y, int width, int height, bool repaint);

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(
        IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern IntPtr ChildWindowFromPointEx(
        IntPtr hwnd, POINT point, uint flags);

    [DllImport("user32.dll")]
    private static extern bool IsChild(IntPtr parent, IntPtr child);

    [DllImport("user32.dll")]
    private static extern bool ScreenToClient(IntPtr hwnd, ref POINT point);

    [DllImport("user32.dll")]
    private static extern bool GetGUIThreadInfo(
        uint threadId, ref GUITHREADINFO info);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool PostMessage(
        IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool PrintWindow(
        IntPtr hwnd, IntPtr deviceContext, uint flags);

    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(
        IntPtr dpiContext);

    public static void EnablePerMonitorDpiAwareness() {
        try {
            SetProcessDpiAwarenessContext(new IntPtr(-4));
        }
        catch {
            // Older Windows versions fall back to the process manifest/default.
        }
    }

    public static bool ActivateOwned(long topValue, int processId) {
        IntPtr top = new IntPtr(topValue);
        GetWindowThreadProcessId(top, out uint owner);
        if (owner != processId) throw new InvalidOperationException("Window ownership changed.");
        if (WindowClass(top) == "#32768") return IsMenuOfForeground(top, processId);
        if (IsIconic(top)) ShowWindow(top, 9);
        uint current = GetCurrentThreadId();
        uint targetThread = GetWindowThreadProcessId(top, out owner);
        uint foregroundThread = GetWindowThreadProcessId(GetForegroundWindow(), out uint ignored);
        bool attachedForeground = foregroundThread != 0 && foregroundThread != current &&
            AttachThreadInput(current, foregroundThread, true);
        bool attachedTarget = targetThread != 0 && targetThread != current &&
            targetThread != foregroundThread && AttachThreadInput(current, targetThread, true);
        try {
            BringWindowToTop(top);
            if (!SetForegroundWindow(top)) {
                SwitchToThisWindow(top, true);
            }
            Thread.Sleep(120);
        }
        finally {
            if (attachedTarget) AttachThreadInput(current, targetThread, false);
            if (attachedForeground) AttachThreadInput(current, foregroundThread, false);
        }
        return GetForegroundWindow() == top;
    }

    private static void BeginForegroundInput(long top, int pid) {
        if (!ActivateOwned(top, pid)) {
            throw new InvalidOperationException("Cannot acquire TradingLab foreground. Unlock the desktop and activate TradingLab; no input was sent.");
        }
        RequireForeground(top, pid);
    }

    private static void RequireForeground(long topValue, int processId) {
        IntPtr top = new IntPtr(topValue);
        IntPtr foreground = GetForegroundWindow();
        GetWindowThreadProcessId(top, out uint owner);
        if (owner != processId || !IsWindowEnabled(top) ||
            (foreground != top && !IsMenuOfForeground(top, processId))) {
            throw new InvalidOperationException(
                "Input stopped: the selected TradingLab window is not foreground. Activate that window before continuing.");
        }
        foreach (int key in new int[] { 0x10, 0x11, 0x12, 0x5B, 0x5C }) {
            if ((GetAsyncKeyState(key) & 0x8000) != 0) {
                throw new InvalidOperationException("Input stopped: release physical modifier keys before continuing.");
            }
        }
    }

    private static bool IsMenuOfForeground(IntPtr top, int processId) {
        if (WindowClass(top) != "#32768") return false;
        uint thread = GetWindowThreadProcessId(top, out uint owner);
        IntPtr foreground = GetForegroundWindow();
        GetWindowThreadProcessId(foreground, out uint foregroundOwner);
        if (owner != processId || foregroundOwner != processId) return false;
        var info = new GUITHREADINFO { cbSize = Marshal.SizeOf(typeof(GUITHREADINFO)) };
        return GetGUIThreadInfo(thread, ref info) && info.hwndMenuOwner != IntPtr.Zero &&
            GetAncestor(info.hwndMenuOwner, 2) == foreground;
    }

    private static void RequirePoint(long topValue, int processId, int x, int y) {
        RequireForeground(topValue, processId);
        IntPtr hit = WindowFromPoint(new POINT { X = x, Y = y });
        GetWindowThreadProcessId(hit, out uint owner);
        if (owner != processId || GetAncestor(hit, 2) != new IntPtr(topValue)) {
            throw new InvalidOperationException("Input stopped: another window covers the selected point.");
        }
    }

    private static INPUT MouseInput(uint flags, uint data = 0) {
        var input = new INPUT { type = 0 };
        input.data.mouse.flags = flags;
        input.data.mouse.mouseData = data;
        return input;
    }

    private static INPUT KeyboardInput(ushort key, bool up, ushort unicode = 0) {
        var input = new INPUT { type = 1 };
        input.data.keyboard.key = key;
        input.data.keyboard.scan = unicode == 0 ? (ushort)MapVirtualKey(key, 0) : unicode;
        input.data.keyboard.flags = (up ? 2u : 0u) | (unicode != 0 ? 4u : 0u);
        if (key >= 0x21 && key <= 0x2E) input.data.keyboard.flags |= 1u;
        return input;
    }

    private static void Send(INPUT[] inputs) {
        if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT))) != inputs.Length) {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Native input was not fully delivered.");
        }
    }

    public static void ForegroundClick(long top, int pid, int x, int y, string button) {
        BeginForegroundInput(top, pid);
        RequirePoint(top, pid, x, y);
        if (!SetCursorPos(x, y)) throw new InvalidOperationException("Could not move the pointer.");
        Thread.Sleep(50);
        RequirePoint(top, pid, x, y);
        uint down = button == "right" ? 8u : 2u;
        uint up = button == "right" ? 16u : 4u;
        Send(new INPUT[] { MouseInput(down), MouseInput(up) });
        if (button == "double") {
            Thread.Sleep(60);
            RequirePoint(top, pid, x, y);
            Send(new INPUT[] { MouseInput(down), MouseInput(up) });
        }
    }

    public static void ForegroundType(long top, int pid, string text) {
        BeginForegroundInput(top, pid);
        foreach (char value in text) {
            RequireForeground(top, pid);
            Send(new INPUT[] { KeyboardInput(0, false, value), KeyboardInput(0, true, value) });
        }
    }

    public static void ForegroundKey(long top, int pid, byte key, byte[] modifiers) {
        BeginForegroundInput(top, pid);
        var inputs = new List<INPUT>();
        foreach (byte modifier in modifiers) inputs.Add(KeyboardInput(modifier, false));
        inputs.Add(KeyboardInput(key, false));
        inputs.Add(KeyboardInput(key, true));
        for (int index = modifiers.Length - 1; index >= 0; index--) {
            inputs.Add(KeyboardInput(modifiers[index], true));
        }
        Send(inputs.ToArray());
    }

    public static void ForegroundScroll(long top, int pid, int x, int y, int clicks) {
        BeginForegroundInput(top, pid);
        RequirePoint(top, pid, x, y);
        if (!SetCursorPos(x, y)) throw new InvalidOperationException("Could not move the pointer.");
        RequirePoint(top, pid, x, y);
        Send(new INPUT[] { MouseInput(0x0800, unchecked((uint)(clicks * 120))) });
    }

    public static void ForegroundPointer(
        long top, int pid, int x, int y, int endX, int endY, bool drag) {
        BeginForegroundInput(top, pid);
        RequirePoint(top, pid, x, y);
        if (!SetCursorPos(x, y)) throw new InvalidOperationException("Could not move the pointer.");
        if (!drag) return;
        RequirePoint(top, pid, endX, endY);
        Send(new INPUT[] { MouseInput(2) });
        try {
            for (int step = 1; step <= 12; step++) {
                int nextX = x + (endX - x) * step / 12;
                int nextY = y + (endY - y) * step / 12;
                RequirePoint(top, pid, nextX, nextY);
                if (!SetCursorPos(nextX, nextY)) throw new InvalidOperationException("Could not drag the pointer.");
                Thread.Sleep(20);
            }
        }
        finally {
            // Release only; never leave the physical mouse button held after an aborted drag.
            Send(new INPUT[] { MouseInput(4) });
        }
    }

    private static string WindowText(IntPtr hwnd) {
        int length = Math.Max(1, GetWindowTextLength(hwnd) + 1);
        var buffer = new StringBuilder(length);
        GetWindowText(hwnd, buffer, buffer.Capacity);
        return buffer.ToString();
    }

    private static string WindowClass(IntPtr hwnd) {
        var buffer = new StringBuilder(256);
        GetClassName(hwnd, buffer, buffer.Capacity);
        return buffer.ToString();
    }

    private static TradingLabUxWindow Describe(IntPtr hwnd, int processId) {
        if (!GetWindowRect(hwnd, out RECT rect)) {
            return null;
        }
        int width = rect.Right - rect.Left;
        int height = rect.Bottom - rect.Top;
        if (width <= 1 || height <= 1) {
            return null;
        }
        return new TradingLabUxWindow {
            Hwnd = hwnd.ToInt64(),
            ProcessId = processId,
            Title = WindowText(hwnd),
            ClassName = WindowClass(hwnd),
            Left = rect.Left,
            Top = rect.Top,
            Width = width,
            Height = height,
            Foreground = hwnd == GetForegroundWindow(),
            Enabled = IsWindowEnabled(hwnd),
        };
    }

    public static TradingLabUxWindow[] WindowsForProcess(int processId) {
        var windows = new List<TradingLabUxWindow>();
        EnumWindows(delegate(IntPtr hwnd, IntPtr unused) {
            GetWindowThreadProcessId(hwnd, out uint owner);
            if (owner == processId && IsWindowVisible(hwnd) && WindowClass(hwnd) != "SysShadow") {
                TradingLabUxWindow item = Describe(hwnd, processId);
                if (item != null) {
                    windows.Add(item);
                }
            }
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    public static TradingLabUxWindow[] ChildWindows(long parentValue, int processId) {
        var windows = new List<TradingLabUxWindow>();
        IntPtr parent = new IntPtr(parentValue);
        EnumChildWindows(parent, delegate(IntPtr hwnd, IntPtr unused) {
            GetWindowThreadProcessId(hwnd, out uint owner);
            if (owner == processId && IsWindowVisible(hwnd)) {
                TradingLabUxWindow item = Describe(hwnd, processId);
                if (item != null) {
                    windows.Add(item);
                }
            }
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    private static bool Contains(TradingLabUxWindow window, int x, int y) {
        return x >= window.Left && x < window.Left + window.Width
            && y >= window.Top && y < window.Top + window.Height;
    }

    public static long OwnedWindowAtPoint(long topValue, int processId, int x, int y) {
        IntPtr current = new IntPtr(topValue);
        GetWindowThreadProcessId(current, out uint owner);
        var top = Describe(current, processId);
        if (owner != processId || top == null || !top.Enabled || !Contains(top, x, y)) {
            return 0;
        }
        // Descend in z-order within the selected window, never a sibling dialog.
        for (int depth = 0; depth < 100; depth++) {
            var point = new POINT { X = x, Y = y };
            if (!ScreenToClient(current, ref point)) {
                return 0;
            }
            IntPtr child = ChildWindowFromPointEx(current, point, 0x0007);
            if (child == IntPtr.Zero || child == current) {
                return current.ToInt64();
            }
            GetWindowThreadProcessId(child, out owner);
            if (owner != processId) {
                return 0;
            }
            current = child;
        }
        throw new InvalidOperationException("Window hierarchy exceeded the inspection bound.");
    }

    private static void PostOwnedMessage(
        IntPtr hwnd, int processId, uint message, IntPtr wParam, IntPtr lParam) {
        GetWindowThreadProcessId(hwnd, out uint owner);
        if (owner != processId || !IsWindowEnabled(hwnd)) {
            throw new InvalidOperationException("Input target is no longer an enabled TradingLab window.");
        }
        if (!PostMessage(hwnd, message, wParam, lParam)) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    private static IntPtr MouseLParam(IntPtr hwnd, int screenX, int screenY) {
        var point = new POINT { X = screenX, Y = screenY };
        if (!ScreenToClient(hwnd, ref point)) {
            throw new InvalidOperationException("ScreenToClient failed.");
        }
        int packed = (point.Y << 16) | (point.X & 0xFFFF);
        return new IntPtr(packed);
    }

    public static void ClickOwned(
        long hwndValue, int processId, int screenX, int screenY, string button) {
        IntPtr hwnd = new IntPtr(hwndValue);
        IntPtr location = MouseLParam(hwnd, screenX, screenY);
        IntPtr screenLocation = new IntPtr((screenY << 16) | (screenX & 0xFFFF));
        int hit = SendMessage(hwnd, 0x0084, IntPtr.Zero, screenLocation).ToInt32();
        if (hit != 1) {
            if (hit != 5 || button != "left") {
                throw new InvalidOperationException(
                    "Only client controls and menu-bar clicks are supported; use the window tool for window chrome.");
            }
            PostOwnedMessage(hwnd, processId, 0x00A1, new IntPtr(hit), screenLocation);
            PostOwnedMessage(hwnd, processId, 0x00A2, new IntPtr(hit), screenLocation);
            return;
        }
        PostOwnedMessage(hwnd, processId, 0x0200, IntPtr.Zero, location);
        Thread.Sleep(60);
        if (button == "right") {
            PostOwnedMessage(hwnd, processId, 0x0204, new IntPtr(0x0002), location);
            PostOwnedMessage(hwnd, processId, 0x0205, IntPtr.Zero, location);
            return;
        }
        PostOwnedMessage(hwnd, processId, 0x0201, new IntPtr(0x0001), location);
        PostOwnedMessage(hwnd, processId, 0x0202, IntPtr.Zero, location);
        int count = button == "double" ? 2 : 1;
        if (count == 2) {
            Thread.Sleep(80);
            PostOwnedMessage(hwnd, processId, 0x0203, new IntPtr(0x0001), location);
            PostOwnedMessage(hwnd, processId, 0x0202, IntPtr.Zero, location);
        }
    }

    public static void ScrollOwned(
        long hwndValue, int processId, int screenX, int screenY, int clicks) {
        IntPtr hwnd = new IntPtr(hwndValue);
        int wheel = clicks * 120;
        IntPtr wheelParam = new IntPtr(wheel << 16);
        int packed = (screenY << 16) | (screenX & 0xFFFF);
        PostOwnedMessage(hwnd, processId, 0x020A, wheelParam, new IntPtr(packed));
    }

    public static void PointerOwned(
        long hwndValue, int processId, int x, int y, int endX, int endY, bool drag) {
        IntPtr hwnd = new IntPtr(hwndValue);
        IntPtr start = MouseLParam(hwnd, x, y);
        if (!drag) {
            PostOwnedMessage(hwnd, processId, 0x0200, IntPtr.Zero, start);
            return;
        }
        PostOwnedMessage(hwnd, processId, 0x0201, new IntPtr(1), start);
        try {
            for (int step = 1; step <= 12; step++) {
                int nextX = x + (endX - x) * step / 12;
                int nextY = y + (endY - y) * step / 12;
                PostOwnedMessage(hwnd, processId, 0x0200, new IntPtr(1), MouseLParam(hwnd, nextX, nextY));
                Thread.Sleep(20);
            }
        }
        finally {
            PostOwnedMessage(hwnd, processId, 0x0202, IntPtr.Zero, MouseLParam(hwnd, endX, endY));
        }
    }

    public static long FocusedWindowForProcess(long topValue, int processId) {
        IntPtr top = new IntPtr(topValue);
        GetWindowThreadProcessId(top, out uint owner);
        if (owner != processId) {
            return 0;
        }
        uint threadId = GetWindowThreadProcessId(top, out owner);
        var info = new GUITHREADINFO();
        info.cbSize = Marshal.SizeOf(typeof(GUITHREADINFO));
        if (GetGUIThreadInfo(threadId, ref info) && info.hwndFocus != IntPtr.Zero) {
            GetWindowThreadProcessId(info.hwndFocus, out uint focusOwner);
            if (focusOwner == processId && (info.hwndFocus == top || IsChild(top, info.hwndFocus))) {
                return info.hwndFocus.ToInt64();
            }
        }
        throw new InvalidOperationException("The selected TradingLab window has no focused control.");
    }

    public static void TypeUnicodeOwned(long hwndValue, int processId, string text) {
        IntPtr hwnd = new IntPtr(hwndValue);
        foreach (char value in text) {
            PostOwnedMessage(hwnd, processId, 0x0102, new IntPtr(value), new IntPtr(1));
        }
    }

    public static void KeyDownOwned(long hwndValue, int processId, byte virtualKey) {
        PostOwnedMessage(
            new IntPtr(hwndValue),
            processId,
            0x0100u,
            new IntPtr(virtualKey),
            IntPtr.Zero
        );
    }

    public static void KeyUpOwned(long hwndValue, int processId, byte virtualKey) {
        PostOwnedMessage(
            new IntPtr(hwndValue),
            processId,
            0x0101u,
            new IntPtr(virtualKey),
            new IntPtr(unchecked((int)0xC0000000))
        );
    }

    public static void Close(long hwndValue, int processId) {
        PostOwnedMessage(new IntPtr(hwndValue), processId, 0x0010, IntPtr.Zero, IntPtr.Zero);
    }

    public static int[] SetWindow(
        long hwndValue, string operation, int width, int height) {
        IntPtr hwnd = new IntPtr(hwndValue);
        if (operation == "maximize") {
            ShowWindow(hwnd, 3);
            return new int[] { 0, 0 };
        }
        if (operation == "minimize") {
            ShowWindow(hwnd, 6);
            return new int[] { 0, 0 };
        }
        ShowWindow(hwnd, 9);
        int minWidth = 0;
        int minHeight = 0;
        if (operation == "resize") {
            int size = Marshal.SizeOf(typeof(MINMAXINFO));
            IntPtr pointer = Marshal.AllocHGlobal(size);
            try {
                Marshal.StructureToPtr(new MINMAXINFO(), pointer, false);
                SendMessage(hwnd, 0x0024, IntPtr.Zero, pointer);
                MINMAXINFO limits = Marshal.PtrToStructure<MINMAXINFO>(pointer);
                minWidth = Math.Max(0, limits.MinTrackSize.X);
                minHeight = Math.Max(0, limits.MinTrackSize.Y);
            }
            finally {
                Marshal.FreeHGlobal(pointer);
            }
            if (!GetWindowRect(hwnd, out RECT rect)) {
                throw new InvalidOperationException("Could not read window bounds.");
            }
            int targetWidth = Math.Max(width, minWidth);
            int targetHeight = Math.Max(height, minHeight);
            if (!MoveWindow(
                hwnd, rect.Left, rect.Top, targetWidth, targetHeight, true)) {
                throw new InvalidOperationException("Could not resize window.");
            }
        }
        return new int[] { minWidth, minHeight };
    }

    public static bool Capture(long hwndValue, IntPtr deviceContext) {
        return PrintWindow(
            new IntPtr(hwndValue),
            deviceContext,
            0x00000002
        );
    }
}
'@
}

[TradingLabUxNative]::EnablePerMonitorDpiAwareness()

function Write-Result([object]$Value) {
    $Value | ConvertTo-Json -Depth 8 -Compress -EscapeHandling EscapeNonAscii
}

function Get-ProcessWindows([int]$ProcessId) {
    if ($ProcessId -le 0) {
        throw "A positive TargetProcessId is required for action '$Action'."
    }
    if (-not $ExpectedExecutablePath -or -not $RunMarker) {
        throw "Executable path and unique run marker are required for every GUI operation."
    }
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    if ($process.HasExited) {
        throw "TradingLab process $ProcessId has exited."
    }
    if ($ExpectedExecutablePath) {
        $expected = (Resolve-Path -LiteralPath $ExpectedExecutablePath).Path
        if (-not $process.Path -or
            [System.IO.Path]::GetFullPath($process.Path) -ne $expected) {
            throw "Process $ProcessId is no longer the expected TradingLab executable."
        }
    }
    if ($RunMarker) {
        $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId"
        $markerArgument = "--ux-run-id=$RunMarker"
        if (-not $identity -or
            -not $identity.CommandLine -or
            -not [regex]::IsMatch($identity.CommandLine, "(?:^|\s)" + [regex]::Escape($markerArgument) + "(?:\s|$)")) {
            throw "Process $ProcessId no longer owns TradingLab UX run $RunMarker."
        }
    }
    $windows = @([TradingLabUxNative]::WindowsForProcess($ProcessId))
    if (-not $windows) {
        throw "No visible windows belong to TradingLab process $ProcessId."
    }
    return $windows
}

function Resolve-Window([int]$ProcessId, [string]$RequestedId) {
    $windows = @(Get-ProcessWindows $ProcessId)
    if ($RequestedId) {
        $clean = $RequestedId -replace '^0x', ''
        $requested = [Convert]::ToInt64($clean, 16)
        $match = $windows | Where-Object { $_.Hwnd -eq $requested } | Select-Object -First 1
        if (-not $match) {
            throw "Window $RequestedId does not belong to TradingLab process $ProcessId."
        }
        return $match
    }
    $foreground = $windows | Where-Object { $_.Foreground } | Select-Object -First 1
    if ($foreground) {
        return $foreground
    }
    return $windows[0]
}

function Convert-Window([object]$Window) {
    return [ordered]@{
        id = "0x{0:X}" -f [int64]$Window.Hwnd
        title = [string]$Window.Title
        class_name = [string]$Window.ClassName
        left = [int]$Window.Left
        top = [int]$Window.Top
        width = [int]$Window.Width
        height = [int]$Window.Height
        foreground = [bool]$Window.Foreground
        enabled = [bool]$Window.Enabled
    }
}

function Get-NormalizedPoint([object]$Window) {
    $x = [int]$Window.Left + [int][Math]::Round(
        ([double]$NormalizedX / 1000.0) * [Math]::Max(0, [int]$Window.Width - 1)
    )
    $y = [int]$Window.Top + [int][Math]::Round(
        ([double]$NormalizedY / 1000.0) * [Math]::Max(0, [int]$Window.Height - 1)
    )
    return @($x, $y)
}

function Get-KeyCode([string]$Name) {
    $codes = @{
        "BACKSPACE" = 0x08
        "TAB" = 0x09
        "ENTER" = 0x0D
        "ESC" = 0x1B
        "SPACE" = 0x20
        "PAGEUP" = 0x21
        "PAGEDOWN" = 0x22
        "END" = 0x23
        "HOME" = 0x24
        "LEFT" = 0x25
        "UP" = 0x26
        "RIGHT" = 0x27
        "DOWN" = 0x28
        "DELETE" = 0x2E
        "," = 0xBC
        "COMMA" = 0xBC
    }
    $upper = $Name.ToUpperInvariant()
    if ($codes.ContainsKey($upper)) {
        return [byte]$codes[$upper]
    }
    if ($upper -match '^F([1-9]|1[0-2])$') {
        return [byte](0x70 + [int]$Matches[1] - 1)
    }
    if ($upper -match '^[A-Z0-9]$') {
        return [byte][char]$upper
    }
    throw "Unsupported key '$Name'."
}

function Send-KeyChord([string]$Chord, [int64]$TargetHwnd) {
    if (-not $Chord) {
        throw "KeyChord is required."
    }
    if ($InputMode -eq "messages" -and $Chord.Contains("+")) {
        throw "Modifier chords require real foreground input; message mode cannot faithfully simulate them. Use visible controls instead."
    }
    $parts = $Chord.ToUpperInvariant().Split("+")
    $key = Get-KeyCode $parts[-1].Trim()
    if ($InputMode -eq "foreground") {
        if (($parts -contains "ALT" -and $parts[-1] -in @("TAB", "ESC", "F4")) -or
            ($parts -contains "CTRL" -and $parts[-1] -in @("ESC", "DELETE"))) {
            throw "System/application-switching chords are not allowed. Use finish to close TradingLab."
        }
        [byte[]]$modifiers = @()
        for ($index = 0; $index -lt $parts.Count - 1; $index++) {
            switch ($parts[$index]) {
                "CTRL" { $modifiers += 0x11 }
                "ALT" { $modifiers += 0x12 }
                "SHIFT" { $modifiers += 0x10 }
                default { throw "Unsupported modifier." }
            }
        }
        [TradingLabUxNative]::ForegroundKey($TargetHwnd, $TargetProcessId, $key, $modifiers)
    } else {
        [TradingLabUxNative]::KeyDownOwned($TargetHwnd, $TargetProcessId, $key)
        [TradingLabUxNative]::KeyUpOwned($TargetHwnd, $TargetProcessId, $key)
    }
}

try {
    switch ($Action) {
        "validate" {
            if (-not $ExecutablePath) {
                throw "ExecutablePath is required."
            }
            $resolved = (Resolve-Path -LiteralPath $ExecutablePath).Path
            $item = Get-Item -LiteralPath $resolved
            $distDirectory = $item.Directory.Parent
            $repositoryRoot = if ($distDirectory) { $distDirectory.Parent } else { $null }
            $versionPath = if ($repositoryRoot) {
                Join-Path $repositoryRoot.FullName "src\tradinglab\_version.py"
            } else {
                ""
            }
            $specPath = if ($repositoryRoot) {
                Join-Path $repositoryRoot.FullName "TradingLab.spec"
            } else {
                ""
            }
            if ($item.Name -ne "TradingLab.exe" -or
                $item.Directory.Name -ne "TradingLab" -or
                -not $distDirectory -or $distDirectory.Name -ne "dist" -or
                -not $repositoryRoot -or
                $repositoryRoot.FullName -ne $TrustedRepositoryRoot -or
                -not (Test-Path -LiteralPath $versionPath) -or
                -not (Test-Path -LiteralPath $specPath)) {
                throw "Only a built dist\TradingLab\TradingLab.exe is allowed."
            }
            $version = $item.VersionInfo
            $versionMatch = [regex]::Match(
                (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8),
                '(?m)^__version__\s*=\s*"([^"]+)"'
            )
            $sourceVersion = $versionMatch.Groups[1].Value
            $productVersion = [string]$version.ProductVersion
            $versionMatches = (
                $productVersion -eq $sourceVersion -or
                $productVersion.StartsWith("$sourceVersion+")
            )
            if ($item.Length -lt 1MB -or
                $version.ProductName -ne "TradingLab" -or
                -not $productVersion -or
                -not $sourceVersion -or
                -not $versionMatches) {
                throw "Executable metadata does not match this TradingLab checkout."
            }
            $bytes = [System.IO.File]::ReadAllBytes($resolved)
            if ([BitConverter]::ToUInt16($bytes, 0) -ne 0x5A4D) {
                throw "Executable does not have a valid PE header."
            }
            $peOffset = [BitConverter]::ToInt32($bytes, 60)
            if ($peOffset -lt 64 -or $peOffset + 6 -ge $bytes.Length -or
                [BitConverter]::ToUInt32($bytes, $peOffset) -ne 0x00004550) {
                throw "Executable does not have a valid PE signature."
            }
            $machine = [BitConverter]::ToUInt16($bytes, $peOffset + 4)
            if ($machine -notin (0x8664, 0xAA64)) {
                throw ("Unsupported TradingLab PE machine 0x{0:X4}." -f $machine)
            }
            Write-Result ([ordered]@{
                executable_path = $resolved
                repository_root = $repositoryRoot.FullName
                product_name = $version.ProductName
                product_version = $version.ProductVersion
                file_version = $version.FileVersion
                pe_machine = "0x{0:X4}" -f $machine
                size_bytes = $item.Length
                sha256 = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash
            })
        }
        "find" {
            if (-not $ExecutablePath) {
                throw "ExecutablePath is required."
            }
            $resolved = (Resolve-Path -LiteralPath $ExecutablePath).Path
            $matches = @()
            $processes = Get-CimInstance Win32_Process -Filter "Name = 'TradingLab.exe'"
            foreach ($process in $processes) {
                    $markerArgument = "--ux-run-id=$RunMarker"
                    $markerMatches = -not $RunMarker -or (
                        $process.CommandLine -and
                        [regex]::IsMatch($process.CommandLine, "(?:^|\s)" + [regex]::Escape($markerArgument) + "(?:\s|$)")
                    )
                    if ($process.ExecutablePath -and $markerMatches -and
                        ($RunMarker -eq "" -or [System.IO.Path]::GetFullPath($process.ExecutablePath) -eq $resolved) -and
                        $process.SessionId -eq (Get-Process -Id $PID).SessionId) {
                        $processId = [int]$process.ProcessId
                        $windows = @([TradingLabUxNative]::WindowsForProcess($processId))
                        $matches += [ordered]@{
                            process_id = $processId
                            windows = @($windows | ForEach-Object { Convert-Window $_ })
                        }
                    }
            }
            Write-Result ([ordered]@{ matches = $matches })
        }
        "inspect" {
            $windows = @(Get-ProcessWindows $TargetProcessId)
            $items = foreach ($window in $windows) {
                $children = @([TradingLabUxNative]::ChildWindows(
                    [int64]$window.Hwnd, $TargetProcessId
                ))
                $entry = Convert-Window $window
                $entry.children = @($children | Select-Object -First 250 |
                    ForEach-Object { Convert-Window $_ })
                $entry
            }
            Write-Result ([ordered]@{
                process_id = $TargetProcessId
                windows = @($items)
            })
        }
        "activate" {
            $window = Resolve-Window $TargetProcessId $WindowId
            $activated = [TradingLabUxNative]::ActivateOwned([int64]$window.Hwnd, $TargetProcessId)
            Write-Result @{ activated = $activated; window = (Convert-Window $window) }
        }
        "capture" {
            if (-not $OutputDirectory) {
                throw "OutputDirectory is required."
            }
            New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
            $windows = @(Get-ProcessWindows $TargetProcessId)
            $captured = @()
            $index = 0
            foreach ($window in $windows | Select-Object -First 8) {
                $index += 1
                $width = [int]$window.Width
                $height = [int]$window.Height
                $bitmap = New-Object System.Drawing.Bitmap($width, $height)
                $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
                $deviceContext = $graphics.GetHdc()
                try {
                    $printed = [TradingLabUxNative]::Capture(
                        [int64]$window.Hwnd,
                        $deviceContext
                    )
                }
                finally {
                    $graphics.ReleaseHdc($deviceContext)
                    $graphics.Dispose()
                }
                if (-not $printed) {
                    $bitmap.Dispose()
                    throw "PrintWindow failed for TradingLab window $("0x{0:X}" -f [int64]$window.Hwnd)."
                }
                $outputBitmap = $bitmap
                if ($width -gt $MaxImageWidth) {
                    $scaledHeight = [Math]::Max(
                        1,
                        [int][Math]::Round($height * ($MaxImageWidth / [double]$width))
                    )
                    $scaled = New-Object System.Drawing.Bitmap($MaxImageWidth, $scaledHeight)
                    $scaledGraphics = [System.Drawing.Graphics]::FromImage($scaled)
                    try {
                        $scaledGraphics.DrawImage(
                            $bitmap,
                            0,
                            0,
                            $MaxImageWidth,
                            $scaledHeight
                        )
                    }
                    finally {
                        $scaledGraphics.Dispose()
                    }
                    $outputBitmap = $scaled
                }

                $path = Join-Path $OutputDirectory ("window-{0:D2}.png" -f $index)
                try {
                    $outputBitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
                }
                finally {
                    if ($outputBitmap -ne $bitmap) {
                        $outputBitmap.Dispose()
                    }
                    $bitmap.Dispose()
                }
                $entry = Convert-Window $window
                $entry.path = (Resolve-Path -LiteralPath $path).Path
                $captured += $entry
            }
            Write-Result ([ordered]@{
                process_id = $TargetProcessId
                windows = $captured
            })
        }
        "click" {
            $window = Resolve-Window $TargetProcessId $WindowId
            $point = Get-NormalizedPoint $window
            $targetHwnd = [TradingLabUxNative]::OwnedWindowAtPoint(
                [int64]$window.Hwnd, $TargetProcessId, $point[0], $point[1]
            )
            if (-not $targetHwnd) {
                throw "Click point is not owned by TradingLab process $TargetProcessId."
            }
            if ($InputMode -eq "foreground") {
                [TradingLabUxNative]::ForegroundClick([int64]$window.Hwnd, $TargetProcessId, $point[0], $point[1], $MouseButton)
            } else {
                [TradingLabUxNative]::ClickOwned($targetHwnd, $TargetProcessId, $point[0], $point[1], $MouseButton)
            }
            Write-Result ([ordered]@{
                action = "click"
                window = Convert-Window $window
                normalized_x = $NormalizedX
                normalized_y = $NormalizedY
                screen_x = $point[0]
                screen_y = $point[1]
                button = $MouseButton
                target_hwnd = "0x{0:X}" -f $targetHwnd
            })
        }
        "type" {
            if (-not $TextFile) {
                throw "TextFile is required."
            }
            $window = Resolve-Window $TargetProcessId $WindowId
            $text = Get-Content -LiteralPath $TextFile -Raw -Encoding UTF8
            if ($InputMode -eq "foreground") {
                [TradingLabUxNative]::ForegroundType([int64]$window.Hwnd, $TargetProcessId, $text)
            } else {
                $targetHwnd = [TradingLabUxNative]::FocusedWindowForProcess([int64]$window.Hwnd, $TargetProcessId)
                [TradingLabUxNative]::TypeUnicodeOwned($targetHwnd, $TargetProcessId, $text)
            }
            Write-Result ([ordered]@{
                action = "type"
                window = Convert-Window $window
                character_count = $text.Length
            })
        }
        "key" {
            $window = Resolve-Window $TargetProcessId $WindowId
            if ($InputMode -eq "foreground") {
                Send-KeyChord $KeyChord ([int64]$window.Hwnd)
            } else {
                $targetHwnd = [TradingLabUxNative]::FocusedWindowForProcess([int64]$window.Hwnd, $TargetProcessId)
                Send-KeyChord $KeyChord $targetHwnd
            }
            Write-Result ([ordered]@{
                action = "key"
                window = Convert-Window $window
                key_chord = $KeyChord
            })
        }
        "scroll" {
            if ($ScrollClicks -eq 0) {
                throw "ScrollClicks must be non-zero."
            }
            $window = Resolve-Window $TargetProcessId $WindowId
            $point = Get-NormalizedPoint $window
            $targetHwnd = [TradingLabUxNative]::OwnedWindowAtPoint(
                [int64]$window.Hwnd, $TargetProcessId, $point[0], $point[1]
            )
            if (-not $targetHwnd) {
                throw "Scroll point is not owned by TradingLab process $TargetProcessId."
            }
            if ($InputMode -eq "foreground") {
                [TradingLabUxNative]::ForegroundScroll([int64]$window.Hwnd, $TargetProcessId, $point[0], $point[1], $ScrollClicks)
            } else {
                [TradingLabUxNative]::ScrollOwned($targetHwnd, $TargetProcessId, $point[0], $point[1], $ScrollClicks)
            }
            Write-Result ([ordered]@{
                action = "scroll"
                window = Convert-Window $window
                normalized_x = $NormalizedX
                normalized_y = $NormalizedY
                scroll_clicks = $ScrollClicks
            })
        }
        "pointer" {
            $window = Resolve-Window $TargetProcessId $WindowId
            $point = Get-NormalizedPoint $window
            $targetHwnd = [TradingLabUxNative]::OwnedWindowAtPoint(
                [int64]$window.Hwnd, $TargetProcessId, $point[0], $point[1]
            )
            if (-not $targetHwnd) { throw "Pointer target is not an enabled TradingLab control." }
            $endPointX = [int]$window.Left + [int][Math]::Round($EndX / 1000.0 * ($window.Width - 1))
            $endPointY = [int]$window.Top + [int][Math]::Round($EndY / 1000.0 * ($window.Height - 1))
            if ($InputMode -eq "foreground") {
                [TradingLabUxNative]::ForegroundPointer(
                    [int64]$window.Hwnd, $TargetProcessId, $point[0], $point[1], $endPointX, $endPointY, ($PointerOperation -eq "drag")
                )
            } else {
                [TradingLabUxNative]::PointerOwned($targetHwnd, $TargetProcessId, $point[0], $point[1], $endPointX, $endPointY, ($PointerOperation -eq "drag"))
            }
            Write-Result @{ action = "pointer"; operation = $PointerOperation; window = (Convert-Window $window) }
        }
        "window" {
            $window = Resolve-Window $TargetProcessId $WindowId
            $minimum = [TradingLabUxNative]::SetWindow(
                [int64]$window.Hwnd,
                $WindowOperation,
                $WindowWidth,
                $WindowHeight
            )
            Start-Sleep -Milliseconds 150
            Write-Result ([ordered]@{
                action = "window"
                window = Convert-Window $window
                operation = $WindowOperation
                width = $WindowWidth
                height = $WindowHeight
                minimum_width = $minimum[0]
                minimum_height = $minimum[1]
            })
        }
        "close" {
            $windows = @(Get-ProcessWindows $TargetProcessId)
            $main = $windows |
                Where-Object { $_.Title -like "TradingLab*" } |
                Select-Object -First 1
            if (-not $main) {
                $main = $windows[-1]
            }
            [TradingLabUxNative]::Close([int64]$main.Hwnd, $TargetProcessId)
            Write-Result ([ordered]@{
                action = "close"
                window = Convert-Window $main
            })
        }
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
