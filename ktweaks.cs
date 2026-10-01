// KS4: Win32 cursor helper only. All ability data lives in ks_profiles.json.
using System.Runtime.InteropServices;
namespace EspOverlay
{
    static class KTweaks
    {
        [StructLayout(LayoutKind.Sequential)]
        public struct PT { public int x, y; }
        [DllImport("user32.dll")]
        public static extern bool GetCursorPos(out PT p);
    }
}
