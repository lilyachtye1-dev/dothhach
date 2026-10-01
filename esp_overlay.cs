// esp_overlay.cs — WPF-оверлей для dota_esp (данные по UDP от esp_feed.py).
//
// Сборка: двойной клик по build_overlay.bat (в папке skibid).
// Запуск: сначала py -3.11 esp_feed.py, затем esp_overlay.exe
// Клавиши: END — выход, F7 — локальный, F8 — только враги.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using System.Web.Script.Serialization;
using System.Linq;

namespace EspOverlay
{
    static class Win32
    {
        [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        public static extern IntPtr FindWindowW(string c, string t);
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int left, top, right, bottom; }
        [StructLayout(LayoutKind.Sequential)] public struct POINT { public int x, y; }
        [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hw, out RECT r);
        [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hw, ref POINT p);
        [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int vk);
        [DllImport("user32.dll")] public static extern int GetWindowLongW(IntPtr hw, int i);
        [DllImport("user32.dll")] public static extern int SetWindowLongW(IntPtr hw, int i, int v);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
    }

    class Hero
    {
        public int idx, team, local, alive, hp, maxhp, level, tp;
        public double mana, maxmana, respawn, x, y, mr = 0.25;
        public string name = "?";
        public List<string[]> abs = new List<string[]>();
        public List<string> items = new List<string>();
        public double sx = -1, sy = -1, shp = 1;
        public double wx, wy;
        public bool hasWorld;
    }

    class State
    {
        public List<Hero> heroes = new List<Hero>();
        public int myTeam;
        public double curtime, displayRange, readerAge;
        public string ksStatus = "KS5: ожидание фида";
        public string ksDetail = "Запустите start.bat. Killsteal включается кнопкой в программе. Quickcast на нажатие.";
        public int ksTarget = -1;
        public bool ksManaged, ksArmed;
        public DashboardData dashboard = new DashboardData();
    }

    public class OverlayVisualSettings
    {
        public int version=2;
        public bool enemiesOnly, showLocal=true, platesEnabled=true, compactEnabled=true;
        public bool heroStripEnabled=false, enemyBadgeEnabled=true, layoutEdit;
        public double size=1.0, opacity=.90;
        public double heroStripX=.50, heroStripY=.90, enemyBadgeX=.50, enemyBadgeY=1.0;
        public void Sanitize()
        {
            if(version!=2)
            {
                version=2;
                enemyBadgeX=.50;enemyBadgeY=1.0; // migrate old hero-pinned coordinates to the fixed bottom HUD
            }
            if(Double.IsNaN(size)||Double.IsInfinity(size))size=1.0;
            if(Double.IsNaN(opacity)||Double.IsInfinity(opacity))opacity=.90;
            if(Double.IsNaN(heroStripX)||Double.IsInfinity(heroStripX))heroStripX=.50;
            if(Double.IsNaN(heroStripY)||Double.IsInfinity(heroStripY))heroStripY=.90;
            if(Double.IsNaN(enemyBadgeX)||Double.IsInfinity(enemyBadgeX))enemyBadgeX=.50;
            if(Double.IsNaN(enemyBadgeY)||Double.IsInfinity(enemyBadgeY))enemyBadgeY=1.0;
            size=Math.Max(.65,Math.Min(1.65,size));opacity=Math.Max(.25,Math.Min(1.0,opacity));
            heroStripX=Math.Max(0,Math.Min(1,heroStripX));heroStripY=Math.Max(0,Math.Min(1,heroStripY));
            enemyBadgeX=Math.Max(0,Math.Min(1,enemyBadgeX));enemyBadgeY=Math.Max(0,Math.Min(1,enemyBadgeY));
        }
    }

    partial class OverlayContent : FrameworkElement
    {
        public OverlayVisualSettings visual;
        public bool enemiesOnly;
        public bool platesEnabled=true, compactEnabled=true, menuOpen;
        public bool showLocal=true;
        public double offX, offY;   // origin клиентской области Dota
        public double curX=-9999, curY=-9999;   // курсор (координаты оверлея)
        State cur=new State();
        Dictionary<int,Hero> smooth=new Dictionary<int,Hero>();
        Dictionary<string,ImageSource> icons=new Dictionary<string,ImageSource>();
        HashSet<string> iconFail=new HashSet<string>();
        Dictionary<string,ImageSource> heroIcons=new Dictionary<string,ImageSource>();
        HashSet<string> heroIconFail=new HashSet<string>();
        Dictionary<string,FormattedText> ftCache=new Dictionary<string,FormattedText>();
        string iconDir=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"dota_icons","abilities");
        Rect heroStripRect=Rect.Empty,enemyBadgeRect=Rect.Empty;
        int dragging;
        Point dragPoint;
        double lastSlowRenderLog;
        Typeface bold=new Typeface(new FontFamily("Segoe UI"),FontStyles.Normal,FontWeights.Bold,FontStretches.Normal);
        Typeface reg=new Typeface(new FontFamily("Segoe UI"),FontStyles.Normal,FontWeights.Normal,FontStretches.Normal);

        public OverlayContent()
        {
            visual=LoadVisualSettings();enemiesOnly=visual.enemiesOnly;showLocal=visual.showLocal;platesEnabled=visual.platesEnabled;compactEnabled=visual.compactEnabled;
            IsHitTestVisible=false;Focusable=false;
            MouseLeftButtonDown+=OverlayMouseDown;MouseMove+=OverlayMouseMove;MouseLeftButtonUp+=OverlayMouseUp;
        }
        static OverlayVisualSettings LoadVisualSettings()
        {
            OverlayVisualSettings value=new OverlayVisualSettings();string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"overlay_visual.json");
            if(File.Exists(path))try{OverlayVisualSettings read=new JavaScriptSerializer().Deserialize<OverlayVisualSettings>(File.ReadAllText(path,Encoding.UTF8));if(read!=null)value=read;}catch(Exception ex){OverlayLog.Write("VISUAL SETTINGS LOAD " + ex.Message);}
            value.Sanitize();return value;
        }
        public void SaveVisualSettings()
        {
            if(visual==null)return;
            visual.enemiesOnly=enemiesOnly;visual.showLocal=showLocal;visual.platesEnabled=platesEnabled;visual.compactEnabled=compactEnabled;visual.Sanitize();
            string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"overlay_visual.json"),tmp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
            try{File.WriteAllText(tmp,new JavaScriptSerializer().Serialize(visual),new UTF8Encoding(false));if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);}
            catch(Exception ex){OverlayLog.Write("VISUAL SETTINGS SAVE " + ex.Message);try{if(File.Exists(tmp))File.Delete(tmp);}catch{}}
        }
        public void ResetPlacement()
        {
            visual.heroStripX=.50;visual.heroStripY=.90;visual.enemyBadgeX=.50;visual.enemyBadgeY=1.0;
            visual.layoutEdit=false;SaveVisualSettings();InvalidateVisual();
        }

        public void Update(State st, double dt)
        {
            cur = st;
            dt=Math.Max(0.001,Math.Min(0.25,dt));
            HashSet<int> seen = new HashSet<int>();
            foreach (var h in st.heroes)
            {
                seen.Add(h.idx);
                Hero s;
                if (!smooth.TryGetValue(h.idx, out s))
                {
                    h.sx = h.x; h.sy = h.y;
                    smooth[h.idx] = h;
                    continue;
                }
                // Copy projected screen coordinates directly: easing creates a visible tail behind moving heroes.
                s.sx=h.x;s.sy=h.y;
                double frac = (h.alive == 1 && h.maxhp > 0)
                    ? Math.Max(0, Math.Min(1, (double)h.hp / h.maxhp)) : 0;
                s.shp += (frac - s.shp) * Math.Min(1, dt * 20);
                s.team = h.team; s.local = h.local; s.alive = h.alive;
                s.hp = h.hp; s.maxhp = h.maxhp; s.mana = h.mana; s.maxmana = h.maxmana;
                s.level = h.level; s.respawn = h.respawn; s.name = h.name;
                s.tp = h.tp; s.abs = h.abs; s.mr = h.mr; s.items = h.items;
                s.wx = h.wx; s.wy = h.wy; s.hasWorld = h.hasWorld;
            }
            List<int> gone = new List<int>();
            foreach (var kv in smooth) if (!seen.Contains(kv.Key)) gone.Add(kv.Key);
            foreach (var g in gone) smooth.Remove(g);
        }

        ImageSource GetIcon(string name)
        {
            ImageSource s;
            if(icons.TryGetValue(name,out s))return s;
            if(iconFail.Contains(name))return null;
            string p=Path.Combine(iconDir,name+".png");
            if(File.Exists(p))
            {
                try{BitmapImage bi=new BitmapImage();bi.BeginInit();bi.UriSource=new Uri(p);bi.CacheOption=BitmapCacheOption.OnLoad;bi.EndInit();bi.Freeze();icons[name]=bi;return bi;}
                catch{iconFail.Add(name);}
            }
            return null;
        }
        ImageSource GetHeroIcon(string name)
        {
            string hero=(name??"").Replace("npc_dota_hero_","").ToLowerInvariant();
            if(!Regex.IsMatch(hero,@"^[a-z0-9_]{1,80}$"))return null;
            ImageSource image;
            if(heroIcons.TryGetValue(hero,out image))return image;
            if(heroIconFail.Contains(hero))return null;
            string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"dota_icons","heroes","icons",hero+".png");
            if(File.Exists(path))
            {
                try{BitmapImage bi=new BitmapImage();bi.BeginInit();bi.UriSource=new Uri(path);bi.CacheOption=BitmapCacheOption.OnLoad;bi.EndInit();bi.Freeze();heroIcons[hero]=bi;return bi;}
                catch{heroIconFail.Add(hero);}
            }
            return null;
        }

        // кэш FormattedText — главная причина лагов при толпе героев
        FormattedText FT(string s, Color c, Typeface tf, double size, float ppd)
        {
            string key = s + "|" + size.ToString("0.##") + "|" + (tf == bold ? "b" : "r")
                         + "|" + c.A + "." + c.R + "." + c.G + "." + c.B;
            FormattedText ft;
            if (!ftCache.TryGetValue(key, out ft))
            {
                ft = new FormattedText(s, CultureInfo.CurrentCulture,
                    FlowDirection.LeftToRight, tf, size, new SolidColorBrush(c), ppd);
                if (ftCache.Count > 1500) ftCache.Clear();
                ftCache[key] = ft;
            }
            return ft;
        }

        // текст с чёрной обводкой; center — центрировать по x
        void DrawText(DrawingContext dc, string s, double x, double y, Color c,
                        Typeface tf, double size, float ppd, bool center)
        {
            FormattedText ft = FT(s, c, tf, size, ppd);
            if (center) x -= ft.Width / 2;
            FormattedText fo = FT(s, Color.FromArgb(235, 0, 0, 0), tf, size, ppd);
            double[][] o = new double[][] {
                new double[]{ -1, 0 }, new double[]{ 1, 0 },
                new double[]{ 0, -1 }, new double[]{ 0, 1 } };
            for (int i = 0; i < 4; i++)
                dc.DrawText(fo, new Point(x + o[i][0], y + o[i][1]));
            dc.DrawText(ft, new Point(x, y));
        }

        protected override void OnRender(DrawingContext dc)
        {
            double renderStarted=KTweaksl.Now;
            base.OnRender(dc);
            float ppd = 1f;
            try { ppd = (float)VisualTreeHelper.GetDpi(this).PixelsPerDip; } catch { }
            bool hasDotaClient=!dotaRect.IsEmpty&&dotaRect.Width>0&&dotaRect.Height>0;
            if(hasDotaClient)
            {
                // Keep every ESP/HUD pixel inside Dota's client rectangle, never over desktop/title bars.
                dc.PushClip(new RectangleGeometry(dotaRect));
                foreach (var h in smooth.Values)
                {
                    bool isLocal = h.local == 1;
                    bool isEnemy = (cur.myTeam == 2 || cur.myTeam == 3)
                                   && (h.team == 2 || h.team == 3) && h.team != cur.myTeam;
                    if (enemiesOnly && !isEnemy && !isLocal) continue;
                    if (isLocal && !showLocal) continue;
                    double px = h.sx * pixelToDipX + offX, py = h.sy * pixelToDipY + offY;
                    // Off-screen heroes are not drawn even when their world-to-screen projection is finite.
                    if(!KTweaksl.Finite(px)||!KTweaksl.Finite(py)||
                       px<dotaRect.Left||py<dotaRect.Top||px>=dotaRect.Right||py>=dotaRect.Bottom)continue;
                    DrawPlate(dc,h,px,py,isLocal,isEnemy,ppd);
                }
                DrawHeroStrip(dc,ppd);
                DrawEnemyBadge(dc,ppd);
                DrawOverlayStatus(dc,ppd);
                DrawInspectionMarkers(dc, ppd);
                DrawLastHitTarget(dc,ppd);
                dc.Pop();
            }
            double renderMs=(KTweaksl.Now-renderStarted)*1000;
            if(renderMs>45&&KTweaksl.Now-lastSlowRenderLog>1.0)
            {
                lastSlowRenderLog=KTweaksl.Now;
                OverlayLog.Write("SLOW RENDER ms="+renderMs.ToString("0.0",CultureInfo.InvariantCulture)+
                    " heroes="+smooth.Count+" badge="+(visual!=null&&visual.enemyBadgeEnabled));
            }
            // KS4 uses only ks_profiles.json; no separate hardcoded hover damage tables.
        }

        List<Hero> VisibleEnemies()
        {
            if(cur.myTeam!=2&&cur.myTeam!=3)return new List<Hero>();
            // `smooth` is populated exclusively from esp_feed's already-filtered packet.
            // Never keep or synthesize a last-seen enemy here: hidden/FOW enemies disappear immediately.
            return smooth.Values.Where(h=>h.local!=1&&(h.team==2||h.team==3)&&h.team!=cur.myTeam)
                .OrderBy(h=>h.idx==cur.ksTarget?0:1).ThenBy(h=>h.idx).Take(5).ToList();
        }
        static double Clamp(double v,double lo,double hi){return Math.Max(lo,Math.Min(hi,v));}
        void DrawHeroStrip(DrawingContext dc,float ppd)
        {
            heroStripRect=Rect.Empty;
            if(visual==null||!visual.heroStripEnabled||dotaRect.IsEmpty)return;
            List<Hero> roster=smooth.Values.OrderBy(h=>h.local==1?0:h.team==cur.myTeam?1:2).ThenBy(h=>h.idx).Take(10).ToList();
            if(roster.Count==0)return;
            double scale=visual.size,size=38*scale,gap=5*scale,cellW=size+9*scale,cellH=size+19*scale;
            double total=roster.Count*cellW+Math.Max(0,roster.Count-1)*gap;
            double center=dotaRect.Left+visual.heroStripX*dotaRect.Width;
            double left=Clamp(center-total/2,dotaRect.Left,dotaRect.Right-total);
            double top=Clamp(dotaRect.Top+visual.heroStripY*dotaRect.Height,dotaRect.Top,dotaRect.Bottom-cellH);
            heroStripRect=new Rect(left,top,total,cellH);
            dc.PushOpacity(visual.opacity);
            dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(190,13,14,20)),new Pen(new SolidColorBrush(Color.FromArgb(155,70,59,91)),1),heroStripRect,8,8);
            for(int i=0;i<roster.Count;i++)
            {
                Hero h=roster[i];double x=left+i*(cellW+gap)+4*scale,y=top+4*scale;
                Rect iconRect=new Rect(x,y,size,size);bool enemy=(h.team==2||h.team==3)&&h.team!=cur.myTeam;
                Color accent=h.local==1?Color.FromRgb(105,221,177):enemy?Color.FromRgb(240,106,116):Color.FromRgb(143,153,215);
                dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(100,accent.R,accent.G,accent.B)),new Pen(new SolidColorBrush(Color.FromArgb(220,accent.R,accent.G,accent.B)),1),iconRect,5,5);
                ImageSource icon=GetHeroIcon(h.name);
                if(icon!=null)dc.DrawImage(icon,new Rect(iconRect.X+1,iconRect.Y+1,iconRect.Width-2,iconRect.Height-2));
                else{string label=(h.name??"?").Replace("npc_dota_hero_","");DrawText(dc,label.Length>0?label.Substring(0,1).ToUpperInvariant():"?",iconRect.X+size/2,iconRect.Y+size/3,accent,bold,14,ppd,true);}
                if(h.alive!=1)dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(155,0,0,0)),null,iconRect,5,5);
                double frac=h.maxhp>0?Clamp((double)h.hp/h.maxhp,0,1):0;
                Rect bar=new Rect(iconRect.X,iconRect.Bottom+2*scale,size,3*scale);
                dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(175,8,9,12)),null,bar,2,2);
                if(frac>0)dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(230,accent.R,accent.G,accent.B)),null,new Rect(bar.X,bar.Y,bar.Width*frac,bar.Height),2,2);
            }
            if(visual.layoutEdit)
            {
                Pen editPen=new Pen(new SolidColorBrush(Color.FromRgb(178,139,255)),1.5);editPen.DashStyle=DashStyles.Dash;
                dc.DrawRoundedRectangle(null,editPen,heroStripRect,8,8);DrawText(dc,"DRAG · ГЕРОИ",heroStripRect.Left+7,heroStripRect.Top+2,Color.FromRgb(220,204,255),bold,7,ppd,false);
            }
            dc.Pop();
        }
        void DrawEnemyBadge(DrawingContext dc,float ppd)
        {
            enemyBadgeRect=Rect.Empty;
            if(visual==null||!visual.enemyBadgeEnabled||dotaRect.IsEmpty)return;
            List<Hero> enemies=VisibleEnemies();
            double scale=visual.size,pad=7*scale,gap=5*scale,headerH=16*scale;
            double innerWidth=Math.Max(1,dotaRect.Width-2*pad);
            double cardW;
            if(enemies.Count==0)cardW=Math.Min(230*scale,innerWidth);
            else cardW=Math.Max(1,Math.Min(156*scale,(innerWidth-gap*(enemies.Count-1))/enemies.Count));
            double cardH=80*scale;
            double cardsWidth=enemies.Count==0?cardW:enemies.Count*cardW+Math.Max(0,enemies.Count-1)*gap;
            double w=2*pad+cardsWidth,h=headerH+cardH+2*pad;
            double left=dotaRect.Left+visual.enemyBadgeX*Math.Max(0,dotaRect.Width-w);
            double top=dotaRect.Top+visual.enemyBadgeY*Math.Max(0,dotaRect.Height-h);
            left=Clamp(left,dotaRect.Left,Math.Max(dotaRect.Left,dotaRect.Right-w));
            top=Clamp(top,dotaRect.Top,Math.Max(dotaRect.Top,dotaRect.Bottom-h));
            enemyBadgeRect=new Rect(left,top,w,h);
            dc.PushOpacity(visual.opacity);

            Color accent=Color.FromRgb(242,111,119);
            Pen outer=new Pen(new SolidColorBrush(Color.FromArgb(220,82,52,62)),1);
            dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(238,15,16,22)),outer,enemyBadgeRect,8,8);
            DrawText(dc,"ВИДИМЫЕ ВРАГИ",left+pad,top+2*scale,Color.FromRgb(236,226,232),bold,7.5*scale,ppd,false);
            DrawText(dc,enemies.Count.ToString(CultureInfo.InvariantCulture)+" / 5",left+w-pad-8*scale,top+2*scale,
                Color.FromRgb(205,132,142),bold,7.5*scale,ppd,true);

            for(int i=0;i<enemies.Count;i++)
            {
                Hero enemy=enemies[i];
                double x=left+pad+i*(cardW+gap),y=top+headerH;
                Rect card=new Rect(x,y,cardW,cardH);
                bool selected=enemy.idx==cur.ksTarget;
                Color edge=selected?Color.FromRgb(255,174,113):Color.FromRgb(117,61,71);
                dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(224,25,21,29)),
                    new Pen(new SolidColorBrush(Color.FromArgb(210,edge.R,edge.G,edge.B)),selected?1.4:.8),card,5,5);

                double heroIconSize=Math.Min(27*scale,cardH-10*scale);
                Rect heroIconRect=new Rect(x+5*scale,y+5*scale,heroIconSize,heroIconSize);
                dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(135,accent.R,accent.G,accent.B)),
                    new Pen(new SolidColorBrush(Color.FromArgb(180,accent.R,accent.G,accent.B)),.8),heroIconRect,4,4);
                ImageSource heroIcon=GetHeroIcon(enemy.name);
                if(heroIcon!=null)dc.DrawImage(heroIcon,new Rect(heroIconRect.X+1,heroIconRect.Y+1,heroIconRect.Width-2,heroIconRect.Height-2));
                else
                {
                    string shortName=(enemy.name??"?").Replace("npc_dota_hero_","").Replace("_"," ");
                    string initial=shortName.Length>0?shortName.Substring(0,1).ToUpperInvariant():"?";
                    DrawText(dc,initial,heroIconRect.X+heroIconSize/2,heroIconRect.Y+heroIconSize/3,
                        Color.FromRgb(255,225,228),bold,12*scale,ppd,true);
                }

                double textX=heroIconRect.Right+5*scale;
                double textW=Math.Max(1,card.Right-textX-4*scale);
                string heroName=MenuTheme.HeroLabel(enemy.name);
                DrawText(dc,heroName,textX,y+4*scale,Color.FromRgb(248,241,246),bold,8.5*scale,ppd,false);

                double hpFraction=enemy.maxhp>0?Clamp((double)enemy.hp/enemy.maxhp,0,1):0;
                string health=enemy.alive==1
                    ?enemy.hp.ToString(CultureInfo.InvariantCulture)+" / "+enemy.maxhp.ToString(CultureInfo.InvariantCulture)+"  "+(int)Math.Round(hpFraction*100)+"%"
                    :"МЁРТВ · "+enemy.respawn.ToString("0",CultureInfo.InvariantCulture)+"с";
                DrawText(dc,health,textX,y+17*scale,
                    enemy.alive==1?Color.FromRgb(219,211,216):Color.FromRgb(255,145,151),reg,7.2*scale,ppd,false);

                double barX=textX,barY=y+31*scale,barH=5*scale;
                double barW=Math.Max(1,textW);
                Rect hpBack=new Rect(barX,barY,barW,barH);
                dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(200,7,8,11)),null,hpBack,2.5,2.5);
                if(enemy.alive==1&&hpFraction>0)
                {
                    Color hpColor=hpFraction>.60?Color.FromRgb(103,190,126):hpFraction>.30?Color.FromRgb(224,178,83):Color.FromRgb(232,91,97);
                    dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(240,hpColor.R,hpColor.G,hpColor.B)),null,
                        new Rect(hpBack.X,hpBack.Y,hpBack.Width*hpFraction,hpBack.Height),2.5,2.5);
                }

                int abilityCount=Math.Min(6,enemy.abs==null?0:enemy.abs.Count);
                if(abilityCount>0)
                {
                    double abilityGap=2*scale;
                    double abilitySize=Math.Max(1,Math.Min(18*scale,(cardW-8*scale-abilityGap*(abilityCount-1))/abilityCount));
                    double abilityWidth=abilityCount*abilitySize+(abilityCount-1)*abilityGap;
                    double abilityX=card.Left+(cardW-abilityWidth)/2;
                    double abilityY=card.Bottom-abilitySize-5*scale;
                    for(int j=0;j<abilityCount;j++)
                    {
                        string[] ability=enemy.abs[j];
                        if(ability==null||ability.Length==0||String.IsNullOrEmpty(ability[0]))continue;
                        string abilityName=ability[0];
                        double cooldown=0,totalCooldown=0;int level=0;
                        if(ability.Length>1)double.TryParse(ability[1],NumberStyles.Any,CultureInfo.InvariantCulture,out cooldown);
                        if(ability.Length>2)double.TryParse(ability[2],NumberStyles.Any,CultureInfo.InvariantCulture,out totalCooldown);
                        if(ability.Length>3)int.TryParse(ability[3],NumberStyles.Any,CultureInfo.InvariantCulture,out level);
                        bool isCasting=ability.Length>4&&ability[4]=="1";
                        double ix=abilityX+j*(abilitySize+abilityGap);
                        Rect iconRect=new Rect(ix,abilityY,abilitySize,abilitySize);
                        Pen iconPen=new Pen(new SolidColorBrush(isCasting?Color.FromArgb(245,255,220,105):Color.FromArgb(160,113,104,120)),isCasting?1.4:.7);
                        dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(170,8,10,14)),iconPen,iconRect,2,2);
                        ImageSource abilityIcon=GetIcon(abilityName);
                        if(abilityIcon!=null)
                        {
                            double iconOpacity=enemy.alive==1?(cooldown>0.05?0.48:(level>0?0.95:0.63)):0.32;
                            dc.PushOpacity(iconOpacity);
                            dc.DrawImage(abilityIcon,new Rect(iconRect.X+1,iconRect.Y+1,Math.Max(1,iconRect.Width-2),Math.Max(1,iconRect.Height-2)));
                            dc.Pop();
                        }
                        else
                        {
                            string initial=abilityName.Length>0?abilityName.Substring(0,1).ToUpperInvariant():"?";
                            DrawText(dc,initial,iconRect.X+abilitySize/2,iconRect.Y+abilitySize/4,
                                Color.FromRgb(202,194,214),bold,Math.Max(6,7*scale),ppd,true);
                        }
                        if(cooldown>.05)
                        {
                            double fraction=totalCooldown>.5?Clamp(cooldown/totalCooldown,0,1):.65;
                            double shadeH=Math.Max(1,(abilitySize-2)*fraction);
                            dc.PushOpacity(.66);
                            dc.DrawRoundedRectangle(Brushes.Black,null,new Rect(iconRect.X+1,
                                iconRect.Y+iconRect.Height-1-shadeH,Math.Max(1,iconRect.Width-2),shadeH),1,1);
                            dc.Pop();
                            string cdText=cooldown>=100?"99+":cooldown.ToString("0",CultureInfo.InvariantCulture);
                            DrawText(dc,cdText,iconRect.X+abilitySize/2,iconRect.Y+abilitySize/2-3.5*scale,
                                Color.FromRgb(255,255,255),bold,Math.Max(5.5,6*scale),ppd,true);
                        }
                        if(level>0)
                        {
                            Rect levelTag=new Rect(iconRect.Right-6*scale,iconRect.Bottom-6*scale,5*scale,5*scale);
                            dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(225,26,22,17)),null,levelTag,1,1);
                            DrawText(dc,level.ToString(CultureInfo.InvariantCulture),levelTag.X+levelTag.Width/2,
                                levelTag.Y-1*scale,Color.FromRgb(255,226,128),bold,5*scale,ppd,true);
                        }
                    }
                }
            }
            if(enemies.Count==0)
            {
                Rect placeholder=new Rect(left+pad,top+headerH,cardW,cardH);
                dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(150,23,23,31)),
                    new Pen(new SolidColorBrush(Color.FromArgb(100,124,120,140)),.8),placeholder,5,5);
                DrawText(dc,"Нет видимых врагов",placeholder.Left+10*scale,placeholder.Top+27*scale,
                    Color.FromRgb(175,170,187),reg,9*scale,ppd,false);
            }
            if(visual.layoutEdit)
            {
                Pen editPen=new Pen(new SolidColorBrush(Color.FromRgb(178,139,255)),1.5);editPen.DashStyle=DashStyles.Dash;
                dc.DrawRoundedRectangle(null,editPen,enemyBadgeRect,8,8);
                DrawText(dc,"DRAG · ВРАГИ",enemyBadgeRect.Left+pad,enemyBadgeRect.Top+1,
                    Color.FromRgb(220,204,255),bold,7*scale,ppd,false);
            }
            dc.Pop();
        }
        public bool IsDraggableAt(Point point)
        {
            return visual!=null&&visual.layoutEdit&&!dotaRect.IsEmpty&&
                ((visual.heroStripEnabled&&!heroStripRect.IsEmpty&&heroStripRect.Contains(point))||
                 (visual.enemyBadgeEnabled&&!enemyBadgeRect.IsEmpty&&enemyBadgeRect.Contains(point)));
        }
        public bool IsLayoutDragging{get{return dragging!=0;}}
        protected override HitTestResult HitTestCore(PointHitTestParameters parameters)
        {
            if(IsDraggableAt(parameters.HitPoint))return new PointHitTestResult(this,parameters.HitPoint);
            return null;
        }
        void OverlayMouseDown(object sender,MouseButtonEventArgs e)
        {
            if(!visual.layoutEdit)return;Point p=e.GetPosition(this);
            if(visual.enemyBadgeEnabled&&enemyBadgeRect.Contains(p))dragging=2;
            else if(visual.heroStripEnabled&&heroStripRect.Contains(p))dragging=1;
            else return;
            dragPoint=p;Mouse.Capture(this);e.Handled=true;
        }
        void OverlayMouseMove(object sender,MouseEventArgs e)
        {
            if(dragging==0||e.LeftButton!=MouseButtonState.Pressed||dotaRect.IsEmpty)return;
            Point p=e.GetPosition(this);double dx=p.X-dragPoint.X,dy=p.Y-dragPoint.Y;dragPoint=p;
            if(dragging==1){visual.heroStripX=Clamp(visual.heroStripX+dx/dotaRect.Width,0,1);visual.heroStripY=Clamp(visual.heroStripY+dy/dotaRect.Height,0,1);}
            else{visual.enemyBadgeX=Clamp(visual.enemyBadgeX+dx/dotaRect.Width,0,1);visual.enemyBadgeY=Clamp(visual.enemyBadgeY+dy/dotaRect.Height,0,1);}
            InvalidateVisual();e.Handled=true;
        }
        void OverlayMouseUp(object sender,MouseButtonEventArgs e)
        {
            if(dragging==0)return;dragging=0;if(Mouse.Captured==this)Mouse.Capture(null);SaveVisualSettings();e.Handled=true;
        }

        void DrawPlate(DrawingContext dc, Hero h, double x, double y,
                       bool isLocal, bool isEnemy, float ppd)
        {
            if (!platesEnabled) return;
            bool alive = h.alive == 1;
            byte r = isLocal ? (byte)90 : isEnemy ? (byte)255 : (byte)90;
            byte g = isLocal ? (byte)255 : isEnemy ? (byte)82 : (byte)166;
            byte b = isLocal ? (byte)140 : isEnemy ? (byte)77 : (byte)255;
            Color tc = Color.FromRgb(r, g, b);

            int n = alive ? Math.Min(6, h.abs.Count) : 0;
            double iconSz = 22, gap = 2;
            double iconsW = n > 0 ? n * (iconSz + gap) - gap : 0;
            double W = Math.Max(100, iconsW);
            double cx = x + 12;              // плашка чуть правее, по центру героя
            double x0 = cx - W / 2;
            // HP-бар над ингейм-баром здоровья, иконки — под героем
            double nameY = y - 104;
            double by = nameY + 13;
            double iy = y + 34;              // иконки чуть ниже

            // предикт каста: кто-то из абилок в ability phase
            bool casting = false;
            for (int i = 0; i < n; i++)
                if (h.abs[i].Length > 4 && h.abs[i][4] == "1") { casting = true; break; }
            if (casting)
            {
                Pen ring = new Pen(new SolidColorBrush(Color.FromArgb(220, 255, 220, 90)), 2);
                dc.DrawEllipse(null, ring, new Point(cx, y + 4), 34, 15);
            }

            // имя + уровень
            string name = h.name.Replace("npc_dota_hero_", "").Replace("_", " ");
            if (name.Length > 14) name = name.Substring(0, 14);
            name = CultureInfo.CurrentCulture.TextInfo.ToTitleCase(name) + " " + h.level;
            DrawText(dc, name, x0, nameY, tc, bold, 11, ppd, false);

            if (!alive)
            {
                DrawText(dc, "DEAD " + h.respawn.ToString("0") + "s", cx, by - 1,
                         Color.FromRgb(255, 115, 115), bold, 12, ppd, true);
            }
            else
            {
                // HP-бар + крупная цифра с обводкой
                dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(140, 8, 8, 10)), null,
                    new Rect(x0, by, W, 8), 2, 2);
                double frac = Math.Max(0, Math.Min(1, h.shp));
                if (frac > 0.004)
                    dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(215, r, g, b)), null,
                        new Rect(x0, by, W * frac, 8), 2, 2);
                DrawText(dc, h.hp.ToString(), cx, by - 2,
                         Color.FromArgb(245, 255, 255, 255), bold, 12, ppd, true);
            }

            // иконки способностей под героем
            if (n > 0)
            {
                double ix = cx - iconsW / 2;
                Pen frame = new Pen(new SolidColorBrush(Color.FromArgb(120, tc.R, tc.G, tc.B)), 1);
                Pen frameCast = new Pen(new SolidColorBrush(Color.FromArgb(230, 255, 220, 90)), 2);
                for (int i = 0; i < n; i++)
                {
                    string[] a = h.abs[i];
                    string an = a[0];
                    double cd, cdl;
                    int lvl = 0;
                    bool isCast = a.Length > 4 && a[4] == "1";
                    double.TryParse(a[1], NumberStyles.Any, CultureInfo.InvariantCulture, out cd);
                    double.TryParse(a[2], NumberStyles.Any, CultureInfo.InvariantCulture, out cdl);
                    if (a.Length > 3) int.TryParse(a[3], out lvl);
                    Rect ir = new Rect(ix, iy, iconSz, iconSz);
                    dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(120, 8, 10, 14)),
                        isCast ? frameCast : frame, ir, 3, 3);
                    ImageSource img = GetIcon(an);
                    if (img != null)
                    {
                        double op = cd > 0.05 ? 0.35 : (lvl > 0 ? 0.83 : 0.5);
                        dc.PushOpacity(op);
                        dc.DrawImage(img, new Rect(ir.X + 1, ir.Y + 1, iconSz - 2, iconSz - 2));
                        dc.Pop();
                    }
                    else
                    {
                        DrawText(dc, an.Substring(0, 1).ToUpper(), ix + 7, iy + 5,
                                 Color.FromArgb(190, 178, 190, 216), reg, 10, ppd, false);
                    }
                    if (cd > 0.05)
                    {
                        double fr = cdl > 0.5 ? Math.Min(1, cd / cdl) : 0.6;
                        double hh = (iconSz - 2) * fr;
                        dc.PushOpacity(0.65);
                        dc.DrawRoundedRectangle(Brushes.Black, null,
                            new Rect(ir.X + 1, ir.Y + 1 + (iconSz - 2) - hh, iconSz - 2, hh), 2, 2);
                        dc.Pop();
                        DrawText(dc, cd.ToString("0"), ix + iconSz / 2, iy + iconSz / 2 - 7,
                                 Color.FromArgb(245, 255, 255, 255), bold, 11, ppd, true);
                    }
                    // пипсы уровня способности
                    if (lvl > 0)
                    {
                        Brush pip = new SolidColorBrush(Color.FromArgb(230, 242, 217, 115));
                        for (int p = 0; p < lvl && p < 4; p++)
                            dc.DrawRoundedRectangle(pip, null,
                                new Rect(ix + 2 + p * 5, iy + iconSz - 5, 4, 4), 1, 1);
                    }
                    ix += iconSz + gap;
                }
            }
        }
    }

    class OverlayWindow : Window
    {
        object lk = new object();
        State latest = new State();
        OverlayContent content = new OverlayContent();
        DispatcherTimer timer;
        Dictionary<int, bool> prevKeys = new Dictionary<int, bool>();
        double geomT;
        double lastPkt;
        double lastStatusLog;
        double lastTickAt;
        double lastSlowTickLog;
        long packetCount;
        string udpError = "";
        bool everPkt;
        MenuWindow menu;
        double menuTick;
        string lastDashboardJson = "";
        DashboardData lastDashboard = new DashboardData();
        IntPtr overlayHandle=IntPtr.Zero;
        bool overlayInteractive;
        JavaScriptSerializer dashboardJson = new JavaScriptSerializer { MaxJsonLength = 65536, RecursionLimit = 32 };

        public OverlayWindow()
        {
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            Topmost = true;
            ShowInTaskbar = false;
            IsHitTestVisible = true;
            ShowActivated = false;
            Focusable = false;
            ResizeMode = ResizeMode.NoResize;
            Content = content;
            menu = new MenuWindow(content);
            Loaded += delegate { menu.Owner = this; menu.Show(); menu.Activate(); };

            Thread thr = new Thread(UdpLoop);
            thr.IsBackground = true;
            thr.Start();

            timer = new DispatcherTimer(DispatcherPriority.Render);
            timer.Interval = TimeSpan.FromMilliseconds(16);
            timer.Tick += Tick;
            timer.Start();
        }

        // ОС-уровневый click-through остаётся включённым вне прямоугольников HUD.
        protected override void OnSourceInitialized(EventArgs e)
        {
            base.OnSourceInitialized(e);
            try
            {
                overlayHandle=new WindowInteropHelper(this).Handle;
                HwndSource source=HwndSource.FromHwnd(overlayHandle);
                if(source!=null)source.AddHook(OverlayHitTestHook);
                ApplyOverlayInputMode(false);
            }
            catch(Exception ex){OverlayLog.Write("HIT TEST INIT " + ex.Message);}
        }
        IntPtr OverlayHitTestHook(IntPtr hwnd,int msg,IntPtr wParam,IntPtr lParam,ref bool handled)
        {
            if(msg!=0x0084||!overlayInteractive)return IntPtr.Zero; // WM_NCHITTEST
            long packed=lParam.ToInt64();int sx=unchecked((short)(packed&0xffff));int sy=unchecked((short)((packed>>16)&0xffff));
            Point point;
            try{point=content.PointFromScreen(new Point(sx,sy));}catch{return IntPtr.Zero;}
            handled=true;return content.IsDraggableAt(point)?new IntPtr(1):new IntPtr(-1); // HTCLIENT / HTTRANSPARENT
        }
        void ApplyOverlayInputMode(bool gameVisible)
        {
            bool edit=gameVisible&&content.visual!=null&&content.visual.layoutEdit&&!menu.IsVisible;
            Point cursor=new Point(content.curX,content.curY);
            bool interactive=edit&&(content.IsLayoutDragging||content.IsDraggableAt(cursor));
            content.IsHitTestVisible=interactive;content.Focusable=false;
            if(overlayHandle==IntPtr.Zero)return;
            try
            {
                int style=Win32.GetWindowLongW(overlayHandle,-20);style|=0x08000000|0x00000080; // NOACTIVATE | TOOLWINDOW
                if(interactive)style&=~0x00000020;else style|=0x00000020; // TRANSPARENT except while dragging/hovering an element
                if(interactive!=overlayInteractive||Win32.GetWindowLongW(overlayHandle,-20)!=style)Win32.SetWindowLongW(overlayHandle,-20,style);
                overlayInteractive=interactive;
            }
            catch(Exception ex){OverlayLog.Write("HIT TEST UPDATE " + ex.Message);}
        }

        void UdpLoop()
        {
            while (true)
            {
                UdpClient udp = null;
                try
                {
                    udp = new UdpClient(new IPEndPoint(IPAddress.Loopback, 27971));
                    udp.Client.ReceiveBufferSize = 262144;
                    OverlayLog.Write("UDP listening on 127.0.0.1:27971");
                    lock (lk) { udpError = ""; }
                    while (true)
                    {
                        IPEndPoint ep = new IPEndPoint(IPAddress.Any, 0);
                        byte[] data = udp.Receive(ref ep);
                        Parse(data);
                    }
                }
                catch (Exception ex)
                {
                    lock (lk) { udpError = ex.GetType().Name + ": " + ex.Message; }
                    OverlayLog.Write("UDP ERROR " + ex.ToString());
                }
                finally { if (udp != null) udp.Close(); }
                Thread.Sleep(500);
            }
        }

        void Parse(byte[] data)
        {
            string text = Encoding.UTF8.GetString(data);
            string[] lines = text.Split('\n');
            if (lines.Length == 0) return;
            string[] head = lines[0].Split('\t');
            if (head.Length < 3 || head[0] != "ESP1") return;
            State st = new State();
            double ct; double.TryParse(head[1], NumberStyles.Any, CultureInfo.InvariantCulture, out ct);
            st.curtime = ct;
            int mt; int.TryParse(head[2], out mt);
            st.myTeam = mt;
            if (head.Length > 3)
                double.TryParse(head[3], NumberStyles.Float, CultureInfo.InvariantCulture, out st.displayRange);
            if (head.Length > 4)
            {
                if (!double.TryParse(head[4], NumberStyles.Float, CultureInfo.InvariantCulture, out st.readerAge) ||
                    !KTweaksl.Finite(st.readerAge) || st.readerAge < 0)
                    st.readerAge = 99999;
            }
            if (head.Length > 5) { st.ksStatus = head[5]; st.ksManaged = true; }
            if (head.Length > 6) int.TryParse(head[6], out st.ksTarget);
            if (head.Length > 7) st.ksArmed = head[7] == "1";
            if (head.Length > 8) st.ksDetail = head[8];
            for (int i = 1; i < lines.Length; i++)
            {
                if (lines[i].StartsWith("UI1\t", StringComparison.Ordinal))
                {
                    string payload = lines[i].Substring(4);
                    if (payload != lastDashboardJson)
                    {
                        lastDashboardJson = payload;
                        try
                        {
                            DashboardData view = dashboardJson.Deserialize<DashboardData>(payload);
                            if(view==null||view.version!=1||(view.last_hit!=null&&view.last_hit.markers!=null&&view.last_hit.markers.Count>32)||view.sources==null||view.items==null||view.catalog==null||
                                view.disabled==null||view.warnings==null||view.sources.Any(x=>x==null)||view.items.Any(x=>x==null)||view.sources.Count>40||view.items.Count>25||
                                view.catalog.Count>64||view.catalog.Any(x=>x==null||x.abilities==null||x.abilities.Count>32||x.abilities.Any(a=>a==null))||
                                !KTweaksl.Finite(view.age)||view.age<0||!KTweaksl.Finite(view.stale_limit)||view.stale_limit<=0)
                                throw new FormatException("Invalid dashboard frame");
                            view.receivedAt = KTweaksl.Now;
                            lastDashboard = view;
                        }
                        catch (Exception ex) { lastDashboard = new DashboardData(); OverlayLog.Write("Dashboard: " + ex.Message); }
                    }
                    st.dashboard = lastDashboard;
                    continue;
                }
                string[] f = lines[i].Split('\t');
                if (f.Length < 15) continue;
                Hero h = new Hero();
                int.TryParse(f[0], out h.idx);
                double.TryParse(f[1], NumberStyles.Any, CultureInfo.InvariantCulture, out h.x);
                double.TryParse(f[2], NumberStyles.Any, CultureInfo.InvariantCulture, out h.y);
                int.TryParse(f[3], out h.team);
                int.TryParse(f[4], out h.local);
                int.TryParse(f[5], out h.alive);
                int.TryParse(f[6], out h.hp);
                int.TryParse(f[7], out h.maxhp);
                double.TryParse(f[8], NumberStyles.Any, CultureInfo.InvariantCulture, out h.mana);
                double.TryParse(f[9], NumberStyles.Any, CultureInfo.InvariantCulture, out h.maxmana);
                int.TryParse(f[10], out h.level);
                double.TryParse(f[11], NumberStyles.Any, CultureInfo.InvariantCulture, out h.respawn);
                h.name = f[12];
                int.TryParse(f[13], out h.tp);
                if (f.Length > 15)
                {
                    double mr;
                    if (double.TryParse(f[15], NumberStyles.Any, CultureInfo.InvariantCulture, out mr))
                        h.mr = mr;
                }
                if (f.Length > 16 && f[16].Length > 0)
                    h.items.AddRange(f[16].Split(';'));
                if (f[14].Length > 0)
                {
                    foreach (string a in f[14].Split(';'))
                    {
                        string[] parts = a.Split(':');
                        if (parts.Length >= 3) h.abs.Add(parts);
                    }
                }
                if (f.Length > 19)
                {
                    h.hasWorld = f[19].Trim() == "1" &&
                        double.TryParse(f[17], NumberStyles.Float, CultureInfo.InvariantCulture, out h.wx) &&
                        double.TryParse(f[18], NumberStyles.Float, CultureInfo.InvariantCulture, out h.wy) &&
                        KTweaksl.Finite(h.wx) && KTweaksl.Finite(h.wy) && Math.Abs(h.wx) < 100000 && Math.Abs(h.wy) < 100000;
                }
                if (!KTweaksl.Finite(h.x) || !KTweaksl.Finite(h.y)) continue;
                st.heroes.Add(h);
            }
            lock (lk) { latest = st; everPkt = true; lastPkt = KTweaksl.Now; packetCount++; udpError = ""; }
        }

        bool KeyClick(int vk)
        {
            bool down = (Win32.GetAsyncKeyState(vk) & 0x8000) != 0;
            bool prev = prevKeys.ContainsKey(vk) && prevKeys[vk];
            prevKeys[vk] = down;
            return down && !prev;
        }

        void Tick(object s, EventArgs e)
        {
            double tickStarted=KTweaksl.Now;
            double frameGap=lastTickAt>0?Math.Max(0,tickStarted-lastTickAt):0.016;
            lastTickAt=tickStarted;
            if (KeyClick(0x23)) { menu.Finish(); Application.Current.Shutdown(); return; }
            bool menuKey = KeyClick(0x2D);
            bool menuModifier = (Win32.GetAsyncKeyState(0x10) & 0x8000) != 0 ||
                                (Win32.GetAsyncKeyState(0x11) & 0x8000) != 0 ||
                                (Win32.GetAsyncKeyState(0x12) & 0x8000) != 0;
            if (menuKey && !menuModifier) menu.ToggleMenu();
            if(KeyClick(0x77)){content.enemiesOnly=!content.enemiesOnly;content.SaveVisualSettings();}
            if(KeyClick(0x76)){content.showLocal=!content.showLocal;content.SaveVisualSettings();}
            // Poll even outside Dota: a held key cannot become a new press on refocus.
            bool toggle = KeyClick(KTweaksl.Key);
            bool cancel = (Win32.GetAsyncKeyState(0x1B) & 0x8000) != 0;
            bool modifiers = (Win32.GetAsyncKeyState(0x10) & 0x8000) != 0 ||
                             (Win32.GetAsyncKeyState(0x11) & 0x8000) != 0 ||
                             (Win32.GetAsyncKeyState(0x12) & 0x8000) != 0;
            State st; bool received; double packetTime; long packets; string error;
            lock (lk) { st = latest; received = everPkt; packetTime = lastPkt; packets = packetCount; error = udpError; }
            double now = KTweaksl.Now;
            double frameDt=Math.Max(0.001,Math.Min(0.25,frameGap));
            double packetAge = received ? Math.Max(0, now - packetTime) : 99999;
            double dataAge = st.readerAge + packetAge;
            // No auto-shutdown and no blanket hide: show diagnostics even without UDP.
            // Data old for >10 seconds is cleared, not displayed as a live hero position.
            bool displayUsable = received && dataAge <= 10.0;
            if (now - geomT > 0.1) { geomT = now; SyncGeom(); }
            IntPtr game = Win32.FindWindowW(null, "Dota 2");
            bool active = game != IntPtr.Zero && KTweaksl.GetForegroundWindow() == game && !KTweaksl.IsIconic(game);
            bool fresh = received && dataAge <= KTweaksl.Timeout;
            content.Visibility = active && !menu.IsVisible ? Visibility.Visible : Visibility.Hidden;
            content.menuOpen = menu.IsVisible;
            if (now - menuTick >= .2)
            {
                menuTick = now;
                menu.UpdateData(st.dashboard, received, packetAge, content.diagnosticText);
            }
            content.inspectionVisualsAvailable = displayUsable&&fresh;
            content.diagnosticText = "KS5 | " + (received ? "UDP " + packets.ToString() : "WAIT UDP :27971") +
                " | heroes " + st.heroes.Count.ToString() + " | game " + (game != IntPtr.Zero ? "YES" : "NOT FOUND") +
                " | focus " + (active ? "YES" : "NO") +
                (received ? " | data " + dataAge.ToString("0.00", CultureInfo.InvariantCulture) + "s" : "") +
                (received && !fresh ? " | STALE / cursor OFF" : "") +
                (!fresh && received ? " | HUD cleared (stale feed)" : "") +
                (error.Length > 0 ? " | UDP ERROR (see overlay.log)" : "");
            if (now - lastStatusLog >= 3.0)
            {
                lastStatusLog = now;
                string sample = "";
                foreach (Hero h in st.heroes)
                {
                    if (sample.Length > 600) break;
                    sample += " [" + h.idx + ":" + h.name + " xy=" + h.x.ToString("0.0", CultureInfo.InvariantCulture) +
                              "," + h.y.ToString("0.0", CultureInfo.InvariantCulture) + " local=" + h.local + "]";
                }
                OverlayLog.Write(content.diagnosticText + " | window=" + Left + "," + Top + " " + Width + "x" + Height +
                    " | content=" + content.ActualWidth + "x" + content.ActualHeight +
                    " | dotaRect=" + content.dotaRect.ToString() + " | scale=" + content.pixelToDipX + "," + content.pixelToDipY + sample);
            }
            KTweaks.PT cpt;
            if (KTweaks.GetCursorPos(out cpt) && PresentationSource.FromVisual(content) != null)
            {
                Point p = content.PointFromScreen(new Point(cpt.x, cpt.y));
                content.curX = p.X; content.curY = p.Y;
            }
            else { content.curX = -9999; content.curY = -9999; }
            // Current-visible HUD must never reuse last-seen enemy data: clear the whole feed once stale.
            content.Update(displayUsable&&fresh ? st : new State(), frameDt);
            content.TickInspection(toggle&&!modifiers,cancel,active&&fresh&&!content.dotaRect.IsEmpty&&!st.ksManaged);
            content.InvalidateVisual();
            ApplyOverlayInputMode(active&&!menu.IsVisible);
            double tickMs=(KTweaksl.Now-tickStarted)*1000;
            if((frameGap>0.08||tickMs>35)&&now-lastSlowTickLog>1.0)
            {
                lastSlowTickLog=now;
                OverlayLog.Write("SLOW UI TICK gap_ms="+(frameGap*1000).ToString("0",CultureInfo.InvariantCulture)+
                    " work_ms="+tickMs.ToString("0.0",CultureInfo.InvariantCulture)+" heroes="+st.heroes.Count+
                    " packet_age="+packetAge.ToString("0.000",CultureInfo.InvariantCulture)+
                    " reader_age="+st.readerAge.ToString("0.000",CultureInfo.InvariantCulture)+
                    " focus="+active+" menu="+menu.IsVisible+" ks="+st.ksStatus);
            }
        }

        // окно = весь виртуальный экран; плашки рисуем со смещением начала Dota
        void SyncGeom()
        {
            PresentationSource source = PresentationSource.FromVisual(content);
            if (source == null || source.CompositionTarget == null) return;
            Matrix fromDevice = source.CompositionTarget.TransformFromDevice;
            double dx = fromDevice.M11, dy = fromDevice.M22;
            content.pixelToDipX = dx; content.pixelToDipY = dy;
            int vx = Win32.GetSystemMetrics(76), vy = Win32.GetSystemMetrics(77);
            int vw = Win32.GetSystemMetrics(78), vh = Win32.GetSystemMetrics(79);
            if (vw > 0 && vh > 0)
            {
                Left = vx * dx; Top = vy * dy; Width = vw * dx; Height = vh * dy;
            }
            content.dotaRect = Rect.Empty;
            Point statusOrigin = content.PointFromScreen(new Point(12, 12));
            content.diagnosticX = statusOrigin.X; content.diagnosticY = statusOrigin.Y;
            IntPtr hw = Win32.FindWindowW(null, "Dota 2");
            if (hw == IntPtr.Zero || KTweaksl.IsIconic(hw)) return;
            Win32.RECT rct;
            if (!Win32.GetClientRect(hw, out rct) || rct.right <= rct.left || rct.bottom <= rct.top) return;
            Win32.POINT p = new Win32.POINT();
            if (!Win32.ClientToScreen(hw, ref p)) return;
            Point origin = content.PointFromScreen(new Point(p.x, p.y));
            content.offX = origin.X; content.offY = origin.Y;
            content.dotaRect = new Rect(origin.X, origin.Y, (rct.right - rct.left) * dx, (rct.bottom - rct.top) * dy);
        }

    }

    static class Program
    {
        [STAThread]
        static void Main()
        {
            OverlayLog.Write("START KS5 HACHBOB / CLR " + Environment.Version.ToString());
            AppDomain.CurrentDomain.UnhandledException += delegate(object sender, UnhandledExceptionEventArgs e)
            { OverlayLog.Write("UNHANDLED " + Convert.ToString(e.ExceptionObject)); };
            try
            {
                // Keep Win32 screen coordinates physical; WPF coordinates are converted explicitly.
                try { KTweaksl.SetProcessDPIAware(); } catch { }
                Application app = new Application();
                app.DispatcherUnhandledException += delegate(object sender, DispatcherUnhandledExceptionEventArgs e)
                { OverlayLog.Write("WPF ERROR " + e.Exception.ToString()); };
                app.Run(new OverlayWindow());
                OverlayLog.Write("EXIT");
            }
            catch (Exception ex)
            {
                OverlayLog.Write("FATAL " + ex.ToString());
                MessageBox.Show("Overlay failed. See overlay.log\n\n" + ex.Message, "KS4");
            }
        }
    }
}
