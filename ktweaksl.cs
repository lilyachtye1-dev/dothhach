// ktweaksl.cs -- cursor inspection toggle and hover markers (no clicks/casts).
// .NET Framework / WPF, C# 5 compatible. Settings: ktweaksl.ini.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Media;

namespace EspOverlay
{
    static class OverlayLog
    {
        static readonly object Gate = new object();
        internal static void Write(string message)
        {
            lock (Gate)
            {
                try
                {
                    string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "overlay.log");
                    if (File.Exists(path) && new FileInfo(path).Length > 2 * 1024 * 1024)
                    {
                        File.Copy(path, path + ".previous", true);
                        File.Delete(path);
                    }
                    File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture) +
                        " " + message + Environment.NewLine);
                }
                catch { } // Never crash the overlay if the folder is read-only.
            }
        }
    }

    static class KTweaksl
    {
        [DllImport("user32.dll")] internal static extern bool SetCursorPos(int x, int y);
        [DllImport("user32.dll")] internal static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] internal static extern bool IsIconic(IntPtr window);
        [DllImport("user32.dll")] internal static extern bool SetProcessDPIAware();
        internal static readonly Dictionary<string, string> Config = Load();
        static Dictionary<string, string> Load()
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                foreach (string line in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "ktweaksl.ini")))
                {
                    string s = line.Trim();
                    if (s.Length == 0 || s.StartsWith("#") || s.StartsWith(";")) continue;
                    int p = s.IndexOf('=');
                    if (p > 0) result[s.Substring(0, p).Trim()] = s.Substring(p + 1).Trim();
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return result;
        }
        internal static double Setting(string key, double fallback, double min, double max)
        {
            string s; double value;
            if (Config.TryGetValue(key, out s) && double.TryParse(s, NumberStyles.Float,
                CultureInfo.InvariantCulture, out value) && Finite(value) && value >= min && value <= max)
                return value;
            return fallback;
        }
        internal static bool Finite(double n) { return !double.IsNaN(n) && !double.IsInfinity(n); }
        internal static double Now { get { return (double)System.Diagnostics.Stopwatch.GetTimestamp() / System.Diagnostics.Stopwatch.Frequency; } }
        internal static readonly int Key = (int)Setting("toggle_vk", 186, 1, 254); // VK_OEM_1 ; / Ж
        internal static readonly double FallbackRange = Setting("fallback_range", 600, 64, 25000);
        internal static readonly double MaxRange = Setting("max_range", 2000, 64, 25000);
        internal static readonly bool UseDisplayRange = Setting("use_display_range", 1, 0, 1) == 1;
        internal static readonly double MarkerSize = Setting("marker_size", 6, 3, 20);
        internal static readonly double HoverRadius = Setting("hover_radius", 9, 3, 30);
        internal static readonly double MarkerY = Setting("marker_y", -30, -120, 40);
        internal static readonly double Timeout = Setting("stale_seconds", 0.35, 0.1, 2);
    }

    // Pure presentation model: module states are independent; stale frames never show green ON.
    internal sealed class CompactHudView
    {
        internal string hero, mode, ks, dodge, reason, last;
        internal bool live, ksOn, dodgeOn;
        static bool Finite(double value) { return !Double.IsNaN(value) && !Double.IsInfinity(value); }
        internal static CompactHudView Build(DashboardData data, double now)
        {
            data=data??new DashboardData();
            DodgeData d=data.dodger??new DodgeData();
            double elapsed=now-data.receivedAt;
            double age=data.age+Math.Max(0,elapsed);
            bool live=data.version==1&&data.fresh&&data.receivedAt>0&&Finite(elapsed)&&elapsed>=0&&
                Finite(data.age)&&data.age>=0&&Finite(age)&&Finite(data.stale_limit)&&
                data.stale_limit>0&&age<=data.stale_limit;
            string mode=data.mode=="AUTO"?"AUTO":data.mode=="PREVIEW"?"PREVIEW":"?";
            string dodgeMode=d.mode=="AUTO"?"AUTO":d.mode=="PREVIEW"?"PREVIEW":"?";
            string last=String.IsNullOrEmpty(d.last_action)?"Нажатий ещё не было":d.last_action;
            if(Finite(d.event_age)&&d.event_age>=0&&Finite(elapsed))
                last+=" · "+Math.Min(9999,Math.Floor(d.event_age+Math.Max(0,elapsed))).ToString("0",CultureInfo.InvariantCulture)+" с назад";
            return new CompactHudView {
                hero=String.IsNullOrEmpty(data.hero)?"Ожидание героя":data.hero,
                mode=live?mode:"НЕТ ДАННЫХ", live=live,
                ksOn=live&&data.armed&&mode=="AUTO",
                dodgeOn=live&&d.armed&&!d.pending&&d.available&&dodgeMode=="AUTO",
                ks="KS · "+(!live?"—":data.armed?(mode=="AUTO"?"ON":mode):"OFF"),
                dodge="DODGER · "+(!live?"—":d.pending?"ОЖИДАНИЕ":!d.armed?"OFF":!d.available?"БЛОК":dodgeMode=="AUTO"?"ON":dodgeMode),
                reason=!live?"Нет свежих данных · статус ввода неизвестен":
                    !String.IsNullOrEmpty(d.compact_reason)?d.compact_reason:d.status??"Нет данных Dodger",
                last="Последнее: "+last
            };
        }
    }

    partial class OverlayContent
    {
        public double pixelToDipX = 1, pixelToDipY = 1;
        public Rect dotaRect = Rect.Empty;
        bool inspectEnabled;
        public bool inspectionVisualsAvailable;
        public string diagnosticText = "KS5 | starting";
        public double diagnosticX = 12, diagnosticY = 12;
        readonly Dictionary<string, FormattedText> compactText = new Dictionary<string, FormattedText>();
        void DrawCompactLine(DrawingContext dc,string text,double x,double y,double width,Color color,
                             Typeface face,double size,float ppd)
        {
            text=(text??"").Replace('\n',' ').Replace('\r',' ').Replace('\t',' ');
            string key=text+"|"+width.ToString("0.##",CultureInfo.InvariantCulture)+"|"+color+"|"+size+"|"+(face==bold)+"|"+ppd;
            FormattedText ft;
            if(!compactText.TryGetValue(key,out ft))
            {
                ft=new FormattedText(text,CultureInfo.CurrentCulture,FlowDirection.LeftToRight,face,size,new SolidColorBrush(color),ppd);
                ft.MaxTextWidth=Math.Max(1,width);ft.MaxLineCount=1;ft.Trimming=TextTrimming.CharacterEllipsis;
                if(compactText.Count>=128)compactText.Clear();
                compactText[key]=ft;
            }
            dc.DrawText(ft,new Point(x,y));
        }
        void DrawOverlayStatus(DrawingContext dc, float ppd)
        {
            if (!compactEnabled || menuOpen || dotaRect.IsEmpty || dotaRect.Width<296 || dotaRect.Height<160) return;
            double width=Math.Min(344,dotaRect.Width-36),height=128;
            double x=dotaRect.Left+18,y=Math.Min(dotaRect.Top+84,dotaRect.Bottom-height-16);
            CompactHudView view=CompactHudView.Build(cur.dashboard,KTweaksl.Now);
            Color muted=Color.FromRgb(157,153,172),green=Color.FromRgb(105,221,177),amber=Color.FromRgb(234,194,140);
            dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(242,19,19,29)),
                new Pen(new SolidColorBrush(Color.FromRgb(70,51,99)),1),new Rect(x,y,width,height),10,10);
            DrawCompactLine(dc,"H A C H B O B",x+14,y+10,width-128,Color.FromRgb(180,145,255),bold,11,ppd);
            DrawCompactLine(dc,"Insert — меню",x+width-103,y+11,89,muted,reg,10,ppd);
            DrawCompactLine(dc,view.hero,x+14,y+33,width-127,Color.FromRgb(180,180,195),reg,11,ppd);
            DrawCompactLine(dc,view.mode,x+width-108,y+33,94,view.live&&view.mode=="AUTO"?muted:amber,bold,10,ppd);
            DrawCompactLine(dc,view.ks,x+14,y+56,110,view.ksOn?green:muted,bold,11,ppd);
            DrawCompactLine(dc,view.dodge,x+135,y+56,width-149,view.dodgeOn?green:view.dodge.EndsWith("OFF",StringComparison.Ordinal)?muted:amber,bold,11,ppd);
            DrawCompactLine(dc,view.reason,x+14,y+80,width-28,view.live?amber:muted,reg,10.5,ppd);
            DrawCompactLine(dc,view.last,x+14,y+102,width-28,muted,reg,10,ppd);
        }
        int inspectTarget = -1;
        double inspectRange;
        string rangeSource = "manual";

        Hero LocalHero()
        {
            foreach (Hero h in cur.heroes) if (h.local == 1) return h;
            return null;
        }
        double GetInspectionRange(Hero me)
        {
            double value = KTweaksl.FallbackRange;
            rangeSource = "manual";
            if (KTweaksl.UseDisplayRange && KTweaksl.Finite(cur.displayRange) && cur.displayRange >= 64 && cur.displayRange <= 25000)
            {
                value = cur.displayRange;
                rangeSource = "display (not cast)";
            }
            // Optional manually maintained per-ability profiles. Not live cast ranges.
            if (rangeSource == "manual" && me != null)
            {
                double profile = 0;
                foreach (string[] a in me.abs)
                {
                    int level;
                    if (a.Length < 4 || !int.TryParse(a[3], out level) || level < 1) continue;
                    profile = Math.Max(profile, KTweaksl.Setting("range." + a[0], 0, 64, 25000));
                }
                foreach (string item in me.items)
                    profile = Math.Max(profile, KTweaksl.Setting("range." + item, 0, 64, 25000));
                if (profile > 0) { value = profile; rangeSource = "profile (manual)"; }
            }
            return Math.Min(KTweaksl.MaxRange, value);
        }
        Point InspectPoint(Hero h)
        {
            return new Point(h.sx * pixelToDipX + offX,
                             (h.sy + KTweaksl.MarkerY) * pixelToDipY + offY);
        }
        bool InspectCandidate(Hero h)
        {
            if (h.local == 1 || h.alive != 1 || h.hp <= 0) return false;
            if ((cur.myTeam != 2 && cur.myTeam != 3) || (h.team != 2 && h.team != 3) || h.team == cur.myTeam) return false;
            Point p = InspectPoint(h);
            double edge = KTweaksl.MarkerSize / 2 + 1;
            return KTweaksl.Finite(p.X) && KTweaksl.Finite(p.Y) && !dotaRect.IsEmpty &&
                p.X >= dotaRect.Left + edge && p.X <= dotaRect.Right - edge &&
                p.Y >= dotaRect.Top + edge && p.Y <= dotaRect.Bottom - edge;
        }
        static double WorldDistance(Hero me, Hero h)
        {
            if (me == null || !me.hasWorld || !h.hasWorld) return double.PositiveInfinity;
            double dx = h.wx - me.wx, dy = h.wy - me.wy;
            return Math.Sqrt(dx * dx + dy * dy); // world XY, not screen pixels
        }
        Hero NearestInspectionTarget(Hero me)
        {
            Hero best = null;
            double distance = double.PositiveInfinity;
            foreach (Hero h in smooth.Values)
            {
                if (!InspectCandidate(h)) continue;
                double d = WorldDistance(me, h);
                if (d <= inspectRange && (d < distance || (d == distance && best != null && h.idx < best.idx)))
                { distance = d; best = h; }
            }
            return best;
        }
        public void StopInspection(string why)
        {
            inspectEnabled = false;
            inspectTarget = -1;
        }
        public void TickInspection(bool toggle, bool cancel, bool available)
        {
            Hero me = LocalHero();
            inspectRange = GetInspectionRange(me);
            if (!available) { StopInspection("OFF / focus or stale feed"); return; }
            if (cancel) { StopInspection("OFF"); return; }
            if (me == null || me.alive != 1 || !me.hasWorld)
            { StopInspection("OFF / no local hero"); return; }
            if (toggle)
            {
                if (inspectEnabled) { StopInspection("OFF"); return; }
                inspectEnabled = true;
            }
            if (!inspectEnabled) return;
            Hero best = NearestInspectionTarget(me);
            inspectTarget = best == null ? -1 : best.idx;
            if (best == null) return; // do not move if nobody is in range
            Point marker = InspectPoint(best);
            Point screen = PointToScreen(marker);
            // Recheck foreground immediately before moving the cursor.
            IntPtr game = Win32.FindWindowW(null, "Dota 2");
            if (game == IntPtr.Zero || KTweaksl.GetForegroundWindow() != game || KTweaksl.IsIconic(game))
            { StopInspection("OFF"); return; }
            if (!KTweaksl.SetCursorPos((int)Math.Round(screen.X), (int)Math.Round(screen.Y)))
            { StopInspection("OFF / cursor error"); return; }
            // Use actual cursor position: Windows may clip the requested move.
            KTweaks.PT actual;
            if (KTweaks.GetCursorPos(out actual))
            {
                Point p = PointFromScreen(new Point(actual.x, actual.y));
                curX = p.X; curY = p.Y;
            }
        }
        Hero HoverInspectionMarker()
        {
            if (!inspectionVisualsAvailable) return null;
            Hero best = null;
            double distance = double.PositiveInfinity;
            foreach (Hero h in smooth.Values)
            {
                if (!InspectCandidate(h)) continue;
                Point p = InspectPoint(h);
                double dx = curX - p.X, dy = curY - p.Y;
                if (Math.Abs(dx) > KTweaksl.HoverRadius || Math.Abs(dy) > KTweaksl.HoverRadius) continue;
                double d = dx * dx + dy * dy;
                if (d < distance || (d == distance && h.idx == inspectTarget)) { distance = d; best = h; }
            }
            return best;
        }
        void DrawInspectionMarkers(DrawingContext dc, float ppd)
        {
            if (!inspectionVisualsAvailable || dotaRect.IsEmpty) return;
            Hero hover = HoverInspectionMarker();
            foreach (Hero h in smooth.Values)
            {
                if (!InspectCandidate(h)) continue;
                Point p = InspectPoint(h);
                bool selected = h.idx == cur.ksTarget || h.idx == inspectTarget || (hover != null && h.idx == hover.idx);
                Brush fill = selected ? Brushes.Lime : Brushes.Gray;
                double sz = KTweaksl.MarkerSize;
                dc.DrawRectangle(fill, new Pen(Brushes.Black, 1), new Rect(p.X - sz / 2, p.Y - sz / 2, sz, sz));
            }

        }
    }
}
