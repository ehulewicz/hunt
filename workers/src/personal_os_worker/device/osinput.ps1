# OS-level input for the drive's press, run by `device/osinput.py` from WSL.
# $Actions is set before this runs: base64 of a JSON list of ops. The first op
# focuses the Chrome window; every later op checks it is still foreground and
# every pointer point (the cursor, for a press or a wheel step) lies inside its
# bounds, else the whole batch stops.
# One JSON line goes back: {"ok", "error", "cursor", "rect", "hwnd"}.
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class OsInput {
    [StructLayout(LayoutKind.Sequential)]
    struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Explicit)]
    struct UNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
    [StructLayout(LayoutKind.Sequential)]
    struct INPUT { public uint type; public UNION u; }
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }
    delegate bool EnumProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int cmd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int max);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr hwnd, StringBuilder text, int max);

    const uint MOVE = 0x0001, LEFTDOWN = 0x0002, LEFTUP = 0x0004, WHEEL = 0x0800;
    const uint ABSOLUTE = 0x8000, VIRTUALDESK = 0x4000;
    const uint EXTENDED = 0x0001, KEYUP = 0x0002, UNICODE = 0x0004;

    public static void DpiAware() {
        // PER_MONITOR_AWARE_V2 is -4: coordinates are physical pixels on every monitor.
        if (!SetProcessDpiAwarenessContext(new IntPtr(-4))) { SetProcessDPIAware(); }
    }

    static void Send(INPUT input) {
        INPUT[] inputs = new INPUT[] { input };
        if (SendInput(1, inputs, Marshal.SizeOf(typeof(INPUT))) != 1) {
            throw new Exception("SendInput was blocked");
        }
    }

    static INPUT Mouse(int dx, int dy, uint data, uint flags) {
        INPUT input = new INPUT();
        input.type = 0;
        input.u.mi.dx = dx; input.u.mi.dy = dy; input.u.mi.mouseData = data; input.u.mi.dwFlags = flags;
        return input;
    }

    static INPUT Key(ushort vk, ushort scan, uint flags) {
        INPUT input = new INPUT();
        input.type = 1;
        input.u.ki.wVk = vk; input.u.ki.wScan = scan; input.u.ki.dwFlags = flags;
        return input;
    }

    public static void MoveTo(int x, int y) {
        // Absolute moves are 0..65535 over the whole virtual desktop.
        int left = GetSystemMetrics(76), top = GetSystemMetrics(77);
        int width = Math.Max(GetSystemMetrics(78) - 1, 1), height = Math.Max(GetSystemMetrics(79) - 1, 1);
        int nx = (int)Math.Round((x - left) * 65535.0 / width);
        int ny = (int)Math.Round((y - top) * 65535.0 / height);
        Send(Mouse(nx, ny, 0, MOVE | ABSOLUTE | VIRTUALDESK));
    }

    public static void LeftDown() { Send(Mouse(0, 0, 0, LEFTDOWN)); }
    public static void LeftUp() { Send(Mouse(0, 0, 0, LEFTUP)); }
    public static void Wheel(int delta) { Send(Mouse(0, 0, unchecked((uint)delta), WHEEL)); }

    public static void Text(string chunk) {
        foreach (char unit in chunk) {
            Send(Key(0, unit, UNICODE));
            Send(Key(0, unit, UNICODE | KEYUP));
        }
    }

    public static void Press(ushort vk, bool extended, ushort[] mods, int holdMs) {
        foreach (ushort mod in mods) { Send(Key(mod, 0, 0)); }
        uint flags = extended ? EXTENDED : 0;
        Send(Key(vk, 0, flags));
        Thread.Sleep(holdMs);
        Send(Key(vk, 0, flags | KEYUP));
        for (int i = mods.Length - 1; i >= 0; i--) { Send(Key(mods[i], 0, KEYUP)); }
    }

    public static IntPtr Find(string title) {
        // A visible top-level Chrome window whose title holds `title`.
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate (IntPtr hwnd, IntPtr lParam) {
            if (!IsWindowVisible(hwnd)) { return true; }
            StringBuilder name = new StringBuilder(256);
            GetClassName(hwnd, name, name.Capacity);
            if (name.ToString() != "Chrome_WidgetWin_1") { return true; }
            StringBuilder text = new StringBuilder(1024);
            GetWindowText(hwnd, text, text.Capacity);
            if (text.Length == 0 || text.ToString().IndexOf(title, StringComparison.OrdinalIgnoreCase) < 0) { return true; }
            found = hwnd;
            return false;
        }, IntPtr.Zero);
        return found;
    }

    public static bool Focus(IntPtr hwnd) {
        for (int attempt = 0; attempt < 5; attempt++) {
            if (IsIconic(hwnd)) { ShowWindow(hwnd, 9); Thread.Sleep(150); }
            if (GetForegroundWindow() == hwnd && !IsIconic(hwnd)) { return true; }
            // A foreground change is refused unless this process sent the last input:
            // hold ALT across it. Skipped when Chrome is already in front, so a lone
            // ALT never reaches Chrome's menu.
            Send(Key(0x12, 0, 0));
            SetForegroundWindow(hwnd);
            Send(Key(0x12, 0, KEYUP));
            BringWindowToTop(hwnd);
            Thread.Sleep(80);
        }
        return GetForegroundWindow() == hwnd && !IsIconic(hwnd);
    }
}
'@

function Finish($ok, $message) {
    $point = New-Object OsInput+POINT
    [void][OsInput]::GetCursorPos([ref]$point)
    $rect = New-Object OsInput+RECT
    if ($script:Hwnd -ne [IntPtr]::Zero) { [void][OsInput]::GetWindowRect($script:Hwnd, [ref]$rect) }
    $out = [ordered]@{ ok = $ok; error = $message; cursor = @($point.X, $point.Y)
        rect = @($rect.Left, $rect.Top, $rect.Right, $rect.Bottom); hwnd = [int64]$script:Hwnd }
    [Console]::Out.WriteLine((ConvertTo-Json -InputObject $out -Compress))
}

function GuardCursor {
    $point = New-Object OsInput+POINT
    [void][OsInput]::GetCursorPos([ref]$point)
    Guard $point.X $point.Y
}

function Guard($x, $y) {
    if ([OsInput]::GetForegroundWindow() -ne $script:Hwnd) { throw 'the Chrome window lost focus' }
    if ($null -eq $x) { return }
    $rect = New-Object OsInput+RECT
    [void][OsInput]::GetWindowRect($script:Hwnd, [ref]$rect)
    if ($x -lt $rect.Left -or $x -ge $rect.Right -or $y -lt $rect.Top -or $y -ge $rect.Bottom) {
        throw "the point $x,$y is outside the Chrome window"
    }
}

$script:Hwnd = [IntPtr]::Zero
$script:Down = $false
try {
    [OsInput]::DpiAware()
    $ops = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Actions)))
    foreach ($op in @($ops)) {
        if ($script:Hwnd -eq [IntPtr]::Zero -and $op.op -ne 'focus') { throw 'the first op focuses the window' }
        switch ($op.op) {
            'focus' {
                $hwnd = if ($op.hwnd) { [IntPtr][int64]$op.hwnd } else { [OsInput]::Find([string]$op.title) }
                if ($hwnd -eq [IntPtr]::Zero) { throw 'no Chrome window was found' }
                $script:Hwnd = $hwnd
                if (-not [OsInput]::Focus($hwnd)) { throw 'the Chrome window could not be brought to the foreground' }
            }
            'move' {
                foreach ($p in $op.points) {
                    [Threading.Thread]::Sleep([int]$p[2])
                    Guard ([int]$p[0]) ([int]$p[1])
                    [OsInput]::MoveTo([int]$p[0], [int]$p[1])
                }
            }
            'down' { GuardCursor; [OsInput]::LeftDown(); $script:Down = $true }
            'up' { [OsInput]::LeftUp(); $script:Down = $false }
            'wait' { [Threading.Thread]::Sleep([int]$op.ms) }
            'text' { Guard $null $null; [OsInput]::Text([string]$op.chunk) }
            'key' {
                Guard $null $null
                [OsInput]::Press([uint16]$op.vk, [bool]$op.ext, [uint16[]]@($op.mods), [int]$op.hold)
            }
            'wheel' { GuardCursor; [OsInput]::Wheel([int]$op.delta) }
            default { throw "unknown op $($op.op)" }
        }
    }
    Finish $true $null
} catch {
    $message = $_.Exception.Message
    # A button this batch pressed is released; nothing else is sent after a failure.
    if ($script:Down) { try { [OsInput]::LeftUp() } catch { } }
    try { Finish $false $message } catch { [Console]::Out.WriteLine((ConvertTo-Json -Compress @{ ok = $false; error = $message })) }
}
