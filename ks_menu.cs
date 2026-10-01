// HACHBOB / native WPF dashboard. C# 5, .NET Framework 4.8.
// Menu controls never send game input. Opening it disarms AUTO through the session guard.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;

namespace EspOverlay
{
    public class SourceData
    {
        public string id="", name="", title="", kind="", profile="", status="", reason="", damage_note="", key="", range="", cast="", damage_type="";
        public bool supported, enabled, ready, catalog, defensive;
        public int level, slot;
        public double cooldown, mana;
        public double? damage;
    }

    public class HeroCatalogData
    {
        public string id="", hero_id="", hero="";
        public List<SourceData> abilities=new List<SourceData>();
    }

    public class DodgeSourceData
    {
        public string name="", title="", kind="", mode="", key="", status="", reason="";
        public bool enabled, supported, owned, ready;
        public int level;
        public double cooldown, mana;
    }
    public class DodgeData
    {
        public bool armed, pending, available, alt_selfcast;
        public string compact_reason="", last_action="Нажатий ещё не было";
        public double event_age=-1;
        public string status="OFF", provider="Нет источника угроз", mode="PREVIEW", mode_hint="", last_event="";
        public List<string> observations=new List<string>();
        public List<string> disabled=new List<string>();
        public List<DodgeSourceData> sources=new List<DodgeSourceData>();
    }
    public class LastHitMarker
    {
        public bool visible;
        public string uid="",state="",label="";
        public int width,height;
        public double x,y,hp,hp_low,hp_high,damage,eta_ms,hero_min,hero_max,armor,reserve,quell,hp_loss,hp_loss_span,age=99999;
        public bool IsFresh(double elapsed)
        {
            foreach(double n in new double[]{x,y,hp,hp_low,hp_high,damage,eta_ms,age,elapsed,hero_min,hero_max,armor,reserve,quell,hp_loss,hp_loss_span})
                if(Double.IsNaN(n)||Double.IsInfinity(n))return false;
            return visible&&age>=0&&elapsed>=0&&age+elapsed<=.20&&hp>0&&damage>=0&&
                width>0&&height>0&&x>8&&x<width-8&&y>.09*height&&y<.79*height;
        }
    }
    public class LastHitData
    {
        public bool enabled,held,deny=true,tower=true,quick_attack,show_marker=true,click_mode,single_hit,active,pause_manual,helper_only;
        public LastHitMarker marker;
        public List<LastHitMarker> markers=new List<LastHitMarker>();
        public string pipeline="",speed_source="",damage_status="",incoming_status="",activation="hold";
        public int key=5,attack_key=65,latency_ms=80,revision=-1,sent,creeps,shots,stops,stop_key=83,damage_reserve;
        public string status="OFF · Last Hit",target="",reason="",reader_error="";
        public double age=99999;
    }
    public class ZoomData
    {
        public int revision=-1,distance;
        public bool haze,farz,busy,available,preview;
        public string status="Авто · камера игры",error="",restore_error="";
    }
    public class DashboardData
    {
        public DodgeData dodger=new DodgeData();
        public LastHitData last_hit=new LastHitData();
        public ZoomData zoom=new ZoomData();
        public int version, revision, level, profiles;
        public long generation;
        public string session="", hero="", hero_id="", team="", mode="AUTO", status="Ожидание reader", detail="", stacks="—", talent="—", stacks_reason="", talent_reason="", toggle="—";
        public bool fresh, armed, arm_pending, alive, inventory_known;
        public double age=99999, hp, maxhp, mana, maxmana, margin, interval, stale_limit=.25;
        public double receivedAt;
        public List<string> disabled=new List<string>(), warnings=new List<string>();
        public List<SourceData> sources=new List<SourceData>(), items=new List<SourceData>();
        public List<HeroCatalogData> catalog=new List<HeroCatalogData>();
    }

    static class MenuTheme
    {
        internal static Brush Brush(string color)
        {
            var b=(SolidColorBrush)new BrushConverter().ConvertFromString(color);b.Freeze();return b;
        }
        internal static Brush Text=Brush("#ECECF5"), Muted=Brush("#9293AA"), Purple=Brush("#A887FF"), Green=Brush("#6DDFB4"), Amber=Brush("#EAC28C");
        internal static TextBlock T(string text,double size,Brush color=null,bool bold=false)
        {
            return new TextBlock { Text=text??"",FontSize=size,Foreground=color??Text,
                FontWeight=bold?FontWeights.SemiBold:FontWeights.Normal,TextTrimming=TextTrimming.CharacterEllipsis,
                VerticalAlignment=VerticalAlignment.Center };
        }
        internal static Border Card(UIElement child,Thickness? padding=null)
        {
            return new Border { Background=Brush("#171820"),BorderBrush=Brush("#292A38"),BorderThickness=new Thickness(1),
                CornerRadius=new CornerRadius(12),Padding=padding??new Thickness(16),Child=child };
        }
        internal static string Number(double n){return n.ToString("0.#",CultureInfo.InvariantCulture);}
        internal static string StateName(string state)
        {
            switch(state)
            {
                case "ready":return "Готов";case "cooldown":return "Перезарядка";case "mana":return "Нет маны";
                case "disabled":return "Выключено";case "unsupported":return "Нет модели";case "empty":return "Пусто";
                case "unlearned":return "Не изучено";case "stale":return "Нет данных";case "pending":return "Ждём CD";
                case "absent":return "Не куплен";case "quarantined":return "Нет ответа CD";case "unavailable":return "Недоступен";
                case "unknown":return "Не прочитано";case "talent_unknown":return "Талант: ?";
                case "stacks_unknown":return "Стаки: ?";case "stats_unknown":return "INT: ?";
                default:return "Недоступен";
            }
        }
        static Dictionary<string,ImageSource> cache=new Dictionary<string,ImageSource>();
        internal static ImageSource Icon(string name){return LoadImage("dota_icons/abilities",name);}
        internal static ImageSource HeroIcon(string heroId){return LoadImage("dota_icons/heroes/icons",heroId);}
        internal static ImageSource HeroArt(string heroId){return LoadImage("dota_icons/heroes",heroId);}
        static ImageSource LoadImage(string folder,string name)
        {
            string clean=(name??"").Replace("npc_dota_hero_","").ToLowerInvariant();
            if(!Regex.IsMatch(clean,@"^[a-z0-9_]{1,100}$"))return null;
            string key=folder+"/"+clean;ImageSource image;
            if(cache.TryGetValue(key,out image))return image;
            string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,folder.Replace('/',Path.DirectorySeparatorChar),clean+".png");
            if(!File.Exists(path))return null;
            try{BitmapImage b=new BitmapImage();b.BeginInit();b.UriSource=new Uri(path);b.CacheOption=BitmapCacheOption.OnLoad;b.EndInit();b.Freeze();cache[key]=b;return b;}
            catch{return null;}
        }
        internal static string HeroLabel(string id)
        {
            string s=(id??"").Replace("npc_dota_hero_","").ToLowerInvariant();
            switch(s)
            {
                case "zuus":return "Zeus";case "queenofpain":return "Queen of Pain";case "skywrath_mage":return "Skywrath Mage";
                case "vengefulspirit":return "Vengeful Spirit";case "necrolyte":return "Necrophos";case "magnataur":return "Magnus";
                case "crystal_maiden":return "Crystal Maiden";case "death_prophet":return "Death Prophet";case "dragon_knight":return "Dragon Knight";
                case "shadow_shaman":return "Shadow Shaman";case "ogre_magi":return "Ogre Magi";default:return CultureInfo.CurrentCulture.TextInfo.ToTitleCase(s.Replace('_',' '));
            }
        }
        internal static void AnimateElement(FrameworkElement element)
        {
            if(element==null)return;
            var move=new TranslateTransform(0,0);element.RenderTransform=move;element.RenderTransformOrigin=new Point(.5,.5);
            Duration quick=new Duration(TimeSpan.FromMilliseconds(120));
            element.MouseEnter+=delegate
            {
                move.BeginAnimation(TranslateTransform.XProperty,new DoubleAnimation(2.0,quick));
                element.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(.97,quick));
            };
            element.MouseLeave+=delegate
            {
                move.BeginAnimation(TranslateTransform.XProperty,new DoubleAnimation(0.0,quick));
                element.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(1.0,quick));
            };
            element.PreviewMouseLeftButtonDown+=delegate
            {
                element.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(.82,new Duration(TimeSpan.FromMilliseconds(65))));
            };
            element.PreviewMouseLeftButtonUp+=delegate
            {
                element.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(.97,quick));
            };
        }
        internal static void AnimateButton(Button button){AnimateElement(button);}
        internal static void Install(FrameworkElement root)
        {
            root.Resources=(ResourceDictionary)XamlReader.Parse(@"
<ResourceDictionary xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml'>
 <Style TargetType='Button'><Setter Property='Foreground' Value='#E9E7F6'/><Setter Property='Background' Value='#22232E'/><Setter Property='BorderBrush' Value='#333441'/><Setter Property='BorderThickness' Value='1'/><Setter Property='Padding' Value='14,10'/><Setter Property='Cursor' Value='Hand'/><Setter Property='FontSize' Value='12'/>
  <Setter Property='Template'><Setter.Value><ControlTemplate TargetType='Button'><Border x:Name='b' CornerRadius='8' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}' Padding='{TemplateBinding Padding}'><ContentPresenter HorizontalAlignment='{TemplateBinding HorizontalContentAlignment}' VerticalAlignment='Center'/></Border><ControlTemplate.Triggers><Trigger Property='IsMouseOver' Value='True'><Setter TargetName='b' Property='Background' Value='#302A43'/><Setter TargetName='b' Property='BorderBrush' Value='#8060BD'/></Trigger><Trigger Property='IsPressed' Value='True'><Setter TargetName='b' Property='Background' Value='#473063'/></Trigger><Trigger Property='IsEnabled' Value='False'><Setter TargetName='b' Property='Opacity' Value='.4'/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType='TextBox'><Setter Property='Foreground' Value='#EEEEF7'/><Setter Property='Background' Value='#191A23'/><Setter Property='CaretBrush' Value='#B190FF'/><Setter Property='BorderBrush' Value='#333441'/><Setter Property='Padding' Value='12,8'/><Setter Property='FontSize' Value='12'/><Setter Property='Template'><Setter.Value><ControlTemplate TargetType='TextBox'><Border x:Name='b' CornerRadius='8' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='1' Padding='{TemplateBinding Padding}'><ScrollViewer x:Name='PART_ContentHost'/></Border><ControlTemplate.Triggers><Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='b' Property='BorderBrush' Value='#A17AE8'/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType='CheckBox'><Setter Property='Foreground' Value='#DCDCEB'/><Setter Property='FontSize' Value='12'/><Setter Property='Cursor' Value='Hand'/><Setter Property='VerticalContentAlignment' Value='Center'/><Setter Property='Template'><Setter.Value><ControlTemplate TargetType='CheckBox'><StackPanel Orientation='Horizontal'><Border x:Name='track' Width='30' Height='17' CornerRadius='9' Background='#3A3C4B' VerticalAlignment='Center'><Ellipse x:Name='thumb' Width='11' Height='11' Fill='#C7C7D6' Margin='3,0' HorizontalAlignment='Left'/></Border><ContentPresenter Margin='9,0,0,0' VerticalAlignment='Center'/></StackPanel><ControlTemplate.Triggers><Trigger Property='IsChecked' Value='True'><Setter TargetName='track' Property='Background' Value='#8C60E5'/><Setter TargetName='thumb' Property='HorizontalAlignment' Value='Right'/><Setter TargetName='thumb' Property='Fill' Value='White'/></Trigger><Trigger Property='IsEnabled' Value='False'><Setter TargetName='track' Property='Opacity' Value='.3'/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType='ScrollBar'><Setter Property='Width' Value='7'/><Setter Property='Background' Value='Transparent'/><Setter Property='Template'><Setter.Value><ControlTemplate TargetType='ScrollBar'><Track x:Name='PART_Track' IsDirectionReversed='True' Orientation='{TemplateBinding Orientation}' Minimum='{TemplateBinding Minimum}' Maximum='{TemplateBinding Maximum}' Value='{TemplateBinding Value}' ViewportSize='{TemplateBinding ViewportSize}'><Track.Thumb><Thumb><Thumb.Template><ControlTemplate TargetType='Thumb'><Border CornerRadius='3' Background='#3A354A'/></ControlTemplate></Thumb.Template></Thumb></Track.Thumb></Track></ControlTemplate></Setter.Value></Setter></Style>
</ResourceDictionary>");
        }
    }

    class SourceTile : Border
    {
        TextBlock title,meta,key,state,value;
        Image icon;
        CheckBox enabled;
        bool updating;
        SourceData current;
        public SourceTile(Action<SourceData,bool> change,Action<SourceData> select)
        {
            Background=MenuTheme.Brush("#181922");BorderBrush=MenuTheme.Brush("#2B2C3A");BorderThickness=new Thickness(1);
            CornerRadius=new CornerRadius(10);Padding=new Thickness(10,9,10,9);Margin=new Thickness(0,0,0,7);Cursor=Cursors.Hand;
            MenuTheme.AnimateElement(this);
            var g=new Grid();g.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(45)});
            g.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(1,GridUnitType.Star)});
            g.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(42)});
            g.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(108)});
            g.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(34)});
            var ib=new Border{Width=34,Height=34,CornerRadius=new CornerRadius(7),Background=MenuTheme.Brush("#332747"),ClipToBounds=true,HorizontalAlignment=HorizontalAlignment.Left};
            icon=new Image{Stretch=Stretch.UniformToFill};var ig=new Grid();var fallback=MenuTheme.T("◇",18,MenuTheme.Purple);fallback.HorizontalAlignment=HorizontalAlignment.Center;ig.Children.Add(fallback);ig.Children.Add(icon);ib.Child=ig;g.Children.Add(ib);
            var text=new StackPanel{VerticalAlignment=VerticalAlignment.Center};title=MenuTheme.T("",12,null,true);meta=MenuTheme.T("",9,MenuTheme.Muted);meta.Margin=new Thickness(0,3,0,0);
            text.Children.Add(title);text.Children.Add(meta);Grid.SetColumn(text,1);g.Children.Add(text);
            key=MenuTheme.T("",10,MenuTheme.Muted,true);key.HorizontalAlignment=HorizontalAlignment.Center;
            var keycap=new Border{Child=key,Padding=new Thickness(5,3,5,3),CornerRadius=new CornerRadius(5),Background=MenuTheme.Brush("#1A1922"),BorderBrush=MenuTheme.Brush("#35323F"),BorderThickness=new Thickness(1),HorizontalAlignment=HorizontalAlignment.Center,VerticalAlignment=VerticalAlignment.Center};
            Grid.SetColumn(keycap,2);g.Children.Add(keycap);
            var states=new StackPanel{VerticalAlignment=VerticalAlignment.Center};state=MenuTheme.T("",10,MenuTheme.Green,true);value=MenuTheme.T("",9,MenuTheme.Muted);value.Margin=new Thickness(0,3,0,0);states.Children.Add(state);states.Children.Add(value);Grid.SetColumn(states,3);g.Children.Add(states);
            enabled=new CheckBox{VerticalAlignment=VerticalAlignment.Center,ToolTip="Переключатель действует на сессию. После изменения включи нужный модуль его кнопкой."};
            MenuTheme.AnimateElement(enabled);
            enabled.Checked+=delegate{if(!updating&&current!=null)change(current,true);};
            enabled.Unchecked+=delegate{if(!updating&&current!=null)change(current,false);};
            Grid.SetColumn(enabled,4);g.Children.Add(enabled);Child=g;
            MouseLeftButtonUp+=delegate(object sender,MouseButtonEventArgs e){if(current!=null)select(current);};
        }
        public void Set(SourceData row,bool live,bool available,HashSet<string> disabled,bool selected=false)
        {
            current=row;updating=true;BorderBrush=MenuTheme.Brush(selected?"#956FC4":"#2B2C3A");Background=MenuTheme.Brush(selected?"#241E30":"#181922");
            title.Text=row.title??row.name;key.Text=row.key??"—";icon.Source=MenuTheme.Icon(row.name);
            bool on=row.supported&&!disabled.Contains(row.profile);
            if(row.catalog)
            {
                meta.Text=(row.kind=="item"?"Предмет":"Способность")+" · профиль каталога";
                state.Text="В каталоге";state.Foreground=MenuTheme.Purple;value.Text="Сеансовый флаг";
                enabled.Visibility=row.supported?Visibility.Visible:Visibility.Hidden;enabled.IsEnabled=available&&row.supported;enabled.IsChecked=on;
                ToolTip=(row.reason??"")+"\n"+(row.name??"");updating=false;return;
            }
            string status=!row.supported?row.status:!on?"disabled":!live?"stale":row.status;
            state.Text=status=="cooldown"?MenuTheme.Number(Math.Max(0,row.cooldown))+" сек":MenuTheme.StateName(status);
            state.Foreground=status=="ready"?MenuTheme.Green:status=="cooldown"?MenuTheme.Purple:status=="unsupported"||status=="empty"||status=="absent"?MenuTheme.Muted:MenuTheme.Amber;
            string type=row.kind=="item"?"Предмет":"Способность";
            bool unknown=status=="unknown"||status=="talent_unknown"||status=="stacks_unknown"||status=="stats_unknown";
            meta.Text=!row.supported||unknown?row.reason:type+" · ур. "+row.level+" · мана "+MenuTheme.Number(row.mana);
            bool lowerBound=!String.IsNullOrEmpty(row.damage_note);
            value.Text=row.defensive?row.damage_type:row.damage.HasValue?(lowerBound?"≥ ~ ":"≈ ")+MenuTheme.Number(row.damage.Value)+" до защиты":"Не участвует в KS";
            enabled.Visibility=row.supported?Visibility.Visible:Visibility.Hidden;enabled.IsEnabled=available&&row.supported;enabled.IsChecked=on;
            ToolTip=(row.reason??"")+(String.IsNullOrEmpty(row.damage_note)?"":"\n"+row.damage_note)+"\n"+(row.name??"");updating=false;
        }
    }

    partial class MenuWindow : Window
    {
        DashboardData data=new DashboardData();
        OverlayContent overlay;
        bool connected,live,syncing,allowClose,suppressVisibilitySend;
        double totalAge;
        int page,pendingRevision;
        string scriptModule="Killsteal";
        string buildKey="",selectedId="",selectedHeroId="",diagnostic="",error="",session="";
        HashSet<string> desired=new HashSet<string>();
        TextBlock pageTitle,pageSubtitle,connection,sideHero,footer,message,selectedDetail;
        StackPanel body,moduleBody;
        ScrollViewer scroll;
        Border modulePanel;
        List<Button> nav=new List<Button>();
        List<Action> refresh=new List<Action>();
        Dictionary<string,FrameworkElement> anchors=new Dictionary<string,FrameworkElement>();
        List<SourceTile> tiles=new List<SourceTile>();
        List<SourceData> shown=new List<SourceData>();
        HashSet<string> dodgeDesired=new HashSet<string>();
        readonly string[] titles={"Killsteal","Настройки героев","Визуал","Настройки"};
        readonly string[] subtitles={"Источники текущего героя · AUTO включается только в игре.","Выбери героя из каталога и включи поддерживаемые способности.","Плашки над героями и нижняя HP/skill-панель включаются независимо.","Общие функции и импорт offsets."};

        public MenuWindow(OverlayContent overlay)
        {
            this.overlay=overlay;Title="HACHBOB";Width=1120;Height=760;MinWidth=900;MinHeight=620;
            WindowStyle=WindowStyle.None;ResizeMode=ResizeMode.CanResizeWithGrip;Background=MenuTheme.Brush("#111219");
            Foreground=MenuTheme.Text;FontFamily=new FontFamily("Segoe UI");Topmost=true;ShowInTaskbar=true;
            WindowStartupLocation=WindowStartupLocation.CenterScreen;Width=Math.Min(Width,SystemParameters.WorkArea.Width-24);Height=Math.Min(Height,SystemParameters.WorkArea.Height-24);
            MinWidth=Math.Min(MinWidth,Width);MinHeight=Math.Min(MinHeight,Height);MenuTheme.Install(this);
            var root=new Grid();root.RowDefinitions.Add(new RowDefinition{Height=new GridLength(44)});root.RowDefinitions.Add(new RowDefinition());root.RowDefinitions.Add(new RowDefinition{Height=new GridLength(31)});
            var top=new Grid{Background=MenuTheme.Brush("#101117")};
            var brand=MenuTheme.T("H A C H B O B   /   OFFLINE ASSISTANT",10,MenuTheme.Muted,true);brand.Margin=new Thickness(20,0,0,0);top.Children.Add(brand);
            var hint=MenuTheme.T("INSERT  ·  МЕНЮ",9,MenuTheme.Muted);hint.HorizontalAlignment=HorizontalAlignment.Right;hint.Margin=new Thickness(0,0,62,0);top.Children.Add(hint);
            var close=new Button{Content="✕",Width=40,Height=29,Margin=new Thickness(0,0,9,0),Padding=new Thickness(0),HorizontalAlignment=HorizontalAlignment.Right,ToolTip="Свернуть меню · Insert"};MenuTheme.AnimateButton(close);close.Click+=delegate{Hide();};top.Children.Add(close);
            top.MouseLeftButtonDown+=delegate(object sender,MouseButtonEventArgs e){if(e.OriginalSource==top||e.OriginalSource==brand){try{DragMove();}catch{}}};root.Children.Add(top);

            var main=new Grid();main.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(178)});main.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(148)});main.ColumnDefinitions.Add(new ColumnDefinition());Grid.SetRow(main,1);root.Children.Add(main);
            var side=new Grid{Background=MenuTheme.Brush("#12131B")};side.RowDefinitions.Add(new RowDefinition{Height=new GridLength(90)});side.RowDefinitions.Add(new RowDefinition());side.RowDefinitions.Add(new RowDefinition{Height=new GridLength(86)});Grid.SetColumn(side,0);main.Children.Add(side);
            var logo=new StackPanel{Margin=new Thickness(20,18,12,0)};logo.Children.Add(MenuTheme.T("❯  HACHBOB",17,MenuTheme.Purple,true));var tagline=MenuTheme.T("ASSISTANT / 05",8,MenuTheme.Muted);tagline.Margin=new Thickness(3,7,0,0);logo.Children.Add(tagline);side.Children.Add(logo);
            var navBox=new StackPanel{Margin=new Thickness(10,4,10,0)};Grid.SetRow(navBox,1);side.Children.Add(navBox);
            string[] navLabels={"Скрипты","Настройки героев","Визуал","Настройки"};string[] glyphs={"◈","♙","▦","⚙"};
            for(int i=0;i<navLabels.Length;i++)
            {
                int n=i;var b=new Button{Height=44,Margin=new Thickness(0,0,0,7),BorderThickness=new Thickness(0),HorizontalContentAlignment=HorizontalAlignment.Stretch,Padding=new Thickness(11,7,8,7)};
                var row=new Grid();row.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(25)});row.ColumnDefinitions.Add(new ColumnDefinition());
                var ico=MenuTheme.T(glyphs[i],16,MenuTheme.Muted,true);ico.HorizontalAlignment=HorizontalAlignment.Center;row.Children.Add(ico);
                var label=MenuTheme.T(navLabels[i],11,MenuTheme.Muted,true);Grid.SetColumn(label,1);row.Children.Add(label);b.Content=row;
                MenuTheme.AnimateButton(b);b.Click+=delegate{SetPage(n);};nav.Add(b);navBox.Children.Add(b);
            }
            var sessionBox=new StackPanel();sessionBox.Children.Add(MenuTheme.T("ТЕКУЩАЯ СЕССИЯ",8,MenuTheme.Muted,true));sideHero=MenuTheme.T("Герой не выбран",11,null,true);sideHero.Margin=new Thickness(0,7,0,0);sessionBox.Children.Add(sideHero);
            var sessionCard=MenuTheme.Card(sessionBox,new Thickness(12));sessionCard.Margin=new Thickness(12,7,12,12);Grid.SetRow(sessionCard,2);side.Children.Add(sessionCard);

            modulePanel=new Border{Background=MenuTheme.Brush("#15151E"),BorderBrush=MenuTheme.Brush("#22232D"),BorderThickness=new Thickness(0,0,1,0)};
            moduleBody=new StackPanel{Margin=new Thickness(12,18,12,0)};modulePanel.Child=moduleBody;Grid.SetColumn(modulePanel,1);main.Children.Add(modulePanel);

            var work=new Grid{Margin=new Thickness(22,18,22,11)};work.RowDefinitions.Add(new RowDefinition{Height=GridLength.Auto});work.RowDefinitions.Add(new RowDefinition());Grid.SetColumn(work,2);main.Children.Add(work);
            var head=new Grid{Margin=new Thickness(0,0,0,16)};var hs=new StackPanel();pageTitle=MenuTheme.T(titles[0],24,null,true);pageSubtitle=MenuTheme.T(subtitles[0],10,MenuTheme.Muted);pageSubtitle.Margin=new Thickness(0,5,0,0);hs.Children.Add(pageTitle);hs.Children.Add(pageSubtitle);head.Children.Add(hs);
            connection=MenuTheme.T("●  ОЖИДАНИЕ",9,MenuTheme.Muted,true);connection.HorizontalAlignment=HorizontalAlignment.Right;connection.VerticalAlignment=VerticalAlignment.Top;connection.Margin=new Thickness(0,8,0,0);head.Children.Add(connection);Grid.SetRow(head,0);work.Children.Add(head);
            body=new StackPanel();scroll=new ScrollViewer{Content=body,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled,Padding=new Thickness(0,0,7,0)};Grid.SetRow(scroll,1);work.Children.Add(scroll);
            footer=MenuTheme.T("●  Локальный интерфейс   ·   AUTO выключен после запуска",9,MenuTheme.Muted);footer.Margin=new Thickness(16,0,16,0);Grid.SetRow(footer,2);root.Children.Add(footer);
            Content=new Border{BorderBrush=MenuTheme.Brush("#373040"),BorderThickness=new Thickness(1),Child=root};
            PreviewKeyDown+=delegate(object sender,KeyEventArgs e){if(CaptureLastHitKey(e))return;if(e.Key==Key.Escape){Hide();e.Handled=true;}};
            Closing+=delegate(object sender,System.ComponentModel.CancelEventArgs e){if(!allowClose){e.Cancel=true;Hide();}};
            IsVisibleChanged+=delegate
            {
                if(allowClose||String.IsNullOrEmpty(session))return;
                if(suppressVisibilitySend){suppressVisibilitySend=false;return;}
                SendControls();
            };
            InitializeLastHit();
            InitializeZoom();
            Rebuild(true);
        }

        public void Finish(){FinishZoom();FinishLastHit();SendControls(true,false,false);allowClose=true;Close();}
        public void ToggleMenu(){if(IsVisible&&WindowState!=WindowState.Minimized)Hide();else{WindowState=WindowState.Normal;Show();Activate();}}
        void SetPage(int n){page=n;selectedId="";Rebuild(true);}
        public void UpdateData(DashboardData next,bool packets,double packetAge,string diag)
        {
            data=next??new DashboardData();if(data.dodger==null)data.dodger=new DodgeData();if(data.dodger.disabled==null)data.dodger.disabled=new List<string>();if(data.dodger.sources==null)data.dodger.sources=new List<DodgeSourceData>();if(data.disabled==null)data.disabled=new List<string>();if(data.warnings==null)data.warnings=new List<string>();if(data.sources==null)data.sources=new List<SourceData>();if(data.items==null)data.items=new List<SourceData>();if(data.catalog==null)data.catalog=new List<HeroCatalogData>();
            diagnostic=diag??"";connected=packets&&packetAge<1.5&&data.version==1;totalAge=data.age+Math.Max(0,KTweaksl.Now-data.receivedAt);
            live=connected&&data.fresh&&KTweaksl.Finite(totalAge)&&totalAge<=data.stale_limit;
            if(session!=data.session){session=data.session;pendingRevision=0;altSelfDesired=data.dodger.alt_selfcast;dodgeDesired=new HashSet<string>(data.dodger.disabled);desired=new HashSet<string>(data.disabled);buildKey="";error="";if(IsVisible&&connected)SendControls();}
            SyncLastHit();
            SyncZoom();
            if(pendingRevision<=data.revision){altSelfDesired=data.dodger.alt_selfcast;dodgeDesired=new HashSet<string>(data.dodger.disabled);desired=new HashSet<string>(data.disabled);pendingRevision=0;}
            if(page==1&&(data.catalog.Count==0||!data.catalog.Any(x=>x!=null&&x.id==selectedHeroId)))selectedHeroId=data.catalog.Count>0&&data.catalog[0]!=null?data.catalog[0].id:"";
            sideHero.Text=String.IsNullOrEmpty(data.hero)?"Герой не выбран":data.hero;
            connection.Text=!connected?"●  НЕТ СВЯЗИ":live?"●  READER ONLINE":"●  ДАННЫЕ УСТАРЕЛИ";connection.Foreground=live?MenuTheme.Green:MenuTheme.Amber;
            footer.Text=error.Length>0?error:pendingRevision>data.revision?"Синхронизация меню…":"Insert — меню   ·   включение на странице модуля   ·   End — выход";
            Rebuild(false);syncing=true;foreach(var action in refresh)action();
            for(int i=0;i<tiles.Count&&i<shown.Count;i++)tiles[i].Set(shown[i],live,connected,page==0&&scriptModule=="Dodger"?dodgeDesired:desired,shown[i].id==selectedId);
            syncing=false;UpdateSelected();
        }

        List<SourceData> Rows()
        {
            if(page==0&&(scriptModule=="Last Hit"||scriptModule=="Камера"))return new List<SourceData>();
            if(page==0&&scriptModule=="Dodger")return data.dodger.sources.Where(x=>x!=null).Select(x=>new SourceData{
                id="dodge:"+x.name,name=x.name,title=x.title,kind=x.kind,profile=x.name,key=x.key,status=x.status,
                supported=x.supported,enabled=x.enabled,ready=x.ready,level=x.level,cooldown=x.cooldown,mana=x.mana,
                reason=x.reason,damage_type=x.mode,defensive=true}).ToList();
            if(page==0)return data.sources.Concat(data.items).Where(x=>x!=null&&x.supported).ToList();
            if(page==1){HeroCatalogData hero=SelectedHero();return hero==null||hero.abilities==null?new List<SourceData>():hero.abilities.Where(x=>x!=null&&x.supported).ToList();}
            return new List<SourceData>();
        }
        HeroCatalogData SelectedHero()
        {
            if(data.catalog==null||data.catalog.Count==0)return null;
            HeroCatalogData hero=data.catalog.FirstOrDefault(x=>x!=null&&x.id==selectedHeroId);
            if(hero==null)hero=data.catalog.FirstOrDefault(x=>x!=null);
            if(hero!=null)selectedHeroId=hero.id;return hero;
        }
        string RowKey(List<SourceData> rows)
        {
            return page+"|"+scriptModule+"|"+data.hero_id+"|"+selectedHeroId+"|"+String.Join(";",rows.Select(x=>x.id));
        }
        void Rebuild(bool force)
        {
            if(body==null)return;
            List<SourceData> rows=Rows();string key=RowKey(rows);
            if(!force&&key==buildKey){shown=rows;return;}
            double offset=force?0:scroll.VerticalOffset;buildKey=key;shown=rows;body.Children.Clear();tiles.Clear();refresh.Clear();anchors.Clear();selectedDetail=null;message=null;
            pageTitle.Text=page==0?scriptModule:titles[page];pageSubtitle.Text=page==0&&scriptModule=="Камера"?"Дистанция и дымка · ZFar автоматически.":page==0&&scriptModule=="Last Hit"?"Добивание / денай · удержание кнопки · расчёт попадания.":page==0&&scriptModule=="Dodger"?"Защита текущего героя · отдельные настройки и включение.":subtitles[page];
            for(int i=0;i<nav.Count;i++){nav[i].Background=MenuTheme.Brush(i==page?"#30243F":"#12131B");nav[i].Foreground=i==page?MenuTheme.Purple:MenuTheme.Muted;}
            BuildModuleSidebar();
            if(page==0){if(scriptModule=="Камера")ZoomPage();else if(scriptModule=="Last Hit")LastHitPage();else if(scriptModule=="Dodger")DodgerPage(rows);else ScriptPage(rows);}else if(page==1)HeroesPage(rows);else if(page==2)VisualPage();else SettingsPage();
            syncing=true;foreach(var action in refresh)action();syncing=false;UpdateSelected();scroll.ScrollToVerticalOffset(offset);
        }
        void BuildModuleSidebar()
        {
            moduleBody.Children.Clear();
            if(page==0)
            {
                SectionLabel(moduleBody,"СКРИПТЫ");moduleBody.Children.Add(ModuleButton("◈","Killsteal",true,delegate{SelectScript("Killsteal");}));
                moduleBody.Children.Add(ModuleButton("◇","Dodger",true,delegate{SelectScript("Dodger");}));
                moduleBody.Children.Add(ModuleButton("◉","Last Hit",true,delegate{SelectScript("Last Hit");}));
                moduleBody.Children.Add(ModuleButton("↔","Камера",true,delegate{SelectScript("Камера");}));
                var hint=MenuTheme.T("Модули управления",9,MenuTheme.Muted);hint.TextWrapping=TextWrapping.Wrap;hint.Margin=new Thickness(7,18,4,0);moduleBody.Children.Add(hint);
            }
            else if(page==1)
            {
                SectionLabel(moduleBody,"ГЕРОИ");var count=MenuTheme.T(data.catalog.Count+" поддерживаются",10,MenuTheme.Purple,true);count.Margin=new Thickness(7,3,2,0);moduleBody.Children.Add(count);
                var hint=MenuTheme.T("Выбор в меню задаёт только сессионные флаги способностей.",9,MenuTheme.Muted);hint.TextWrapping=TextWrapping.Wrap;hint.Margin=new Thickness(7,13,4,0);moduleBody.Children.Add(hint);
            }
            else if(page==2)
            {
                SectionLabel(moduleBody,"HUD");moduleBody.Children.Add(ModuleButton("▦","Размещение",true,delegate{ScrollToAnchor("placement");}));
                moduleBody.Children.Add(ModuleButton("◉","Иконки героев",true,delegate{ScrollToAnchor("hero-row");}));
                moduleBody.Children.Add(ModuleButton("♥","Плашка HP",true,delegate{ScrollToAnchor("enemy-badge");}));
            }
            else
            {
                SectionLabel(moduleBody,"ОБЩЕЕ");moduleBody.Children.Add(ModuleButton("⇧","Импорт offsets",true,delegate{ScrollToAnchor("offsets");}));
                moduleBody.Children.Add(ModuleButton("⌁","Журналы",true,delegate{ScrollToAnchor("logs");}));
            }
        }
        static void SectionLabel(Panel parent,string text){var t=MenuTheme.T(text,8,MenuTheme.Muted,true);t.Margin=new Thickness(7,5,0,12);parent.Children.Add(t);}
        Button ModuleButton(string glyph,string text,bool enabled,Action click=null)
        {
            var b=new Button{Height=38,Margin=new Thickness(0,0,0,5),Padding=new Thickness(8,6,8,6),HorizontalContentAlignment=HorizontalAlignment.Left,IsEnabled=enabled,BorderThickness=new Thickness(0),Background=MenuTheme.Brush(page==0&&text==scriptModule?"#292236":"#15151E"),ToolTip=text};
            var row=new StackPanel{Orientation=Orientation.Horizontal};var ico=MenuTheme.T(glyph,13,MenuTheme.Purple,true);ico.Width=23;row.Children.Add(ico);row.Children.Add(MenuTheme.T(text,10,enabled?MenuTheme.Text:MenuTheme.Muted,true));b.Content=row;
            MenuTheme.AnimateButton(b);if(click!=null)b.Click+=delegate{click();};return b;
        }
        void ScriptPage(List<SourceData> rows)
        {
            HeroCard();KillstealArmCard();Stats();
            List<SourceData> abilities=rows.Where(x=>x.kind=="ability").ToList();List<SourceData> items=rows.Where(x=>x.kind=="item").ToList();
            if(abilities.Count==0&&items.Count==0)
            {
                Section("ИСТОЧНИКИ КИЛЛСТИЛА");Note(String.IsNullOrEmpty(data.hero)?"Ожидаем данные из Dota 2. Герой и его способности определяются автоматически.":"Для текущего локального героя пока нет поддерживаемых источников.");
            }
            if(abilities.Count>0){Section("ПОДДЕРЖИВАЕМЫЕ СПОСОБНОСТИ");foreach(SourceData row in abilities)AddTile(row);}
            if(items.Count>0){Section("ПОДДЕРЖИВАЕМЫЕ ПРЕДМЕТЫ");foreach(SourceData row in items)AddTile(row);}
            Note("«Готов» — хватает ресурсов; это не гарантия убийства. Переключатели действуют только в этой сессии. Killsteal включается кнопкой выше; F6 не переключает Killsteal.");
            AddSelectedDetail("Нажми на источник, чтобы увидеть текущее состояние и причину блокировки. Пользовательский каталог способностей здесь не редактируется.");
            message=MenuTheme.T("",10,MenuTheme.Muted);message.TextWrapping=TextWrapping.Wrap;message.LineHeight=17;body.Children.Add(message);refresh.Add(delegate{message.Text=data.status+"\n"+data.detail;});
        }
        void SelectScript(string module)
        {
            if(module!="Killsteal"&&module!="Dodger"&&module!="Last Hit"&&module!="Камера")return;
            scriptModule=module;selectedId="";buildKey="";Rebuild(true);scroll.ScrollToTop();
        }
        bool altSelfDesired;
        void DodgerPage(List<SourceData> rows)
        {
            HeroCard();
            var state=MenuTheme.T("",11,MenuTheme.Purple,true);state.TextWrapping=TextWrapping.Wrap;
            var mode=MenuTheme.T("",10,MenuTheme.Amber,true);mode.TextWrapping=TextWrapping.Wrap;mode.Margin=new Thickness(0,5,0,4);
            var hint=MenuTheme.T("",9,MenuTheme.Muted);hint.TextWrapping=TextWrapping.Wrap;
            var arm=new Button{Margin=new Thickness(0,12,0,0),FontWeight=FontWeights.SemiBold,Background=MenuTheme.Brush("#3A2854"),BorderBrush=MenuTheme.Brush("#8963BF")};MenuTheme.AnimateButton(arm);
            var combined=new CheckBox{Content="Запустить также Killsteal",Margin=new Thickness(0,10,0,0)};
            arm.Click+=delegate
            {
                if(!connected||!data.dodger.available)return;
                if(data.dodger.armed||data.dodger.pending){SendControls(null,null,false);return;}
                suppressVisibilitySend=true;
                if(!SendControls(false,combined.IsChecked==true?(bool?)true:null,true)){suppressVisibilitySend=false;return;}
                Hide();
            };
            var fastSelf=new CheckBox{Content="Alt self-cast: Lotus / Eul / Wind Waker",Margin=new Thickness(0,9,0,0),ToolTip="Только если в Dota включён Smart Double Tap и Alt — self-cast. Тогда без перевода курсора. По умолчанию прежнее наведение на героя."};
            fastSelf.Checked+=delegate{if(syncing)return;altSelfDesired=true;SendControls();};
            fastSelf.Unchecked+=delegate{if(syncing)return;altSelfDesired=false;SendControls();};
            refresh.Add(delegate{fastSelf.IsChecked=altSelfDesired;fastSelf.IsEnabled=connected;});
            var stack=new StackPanel();stack.Children.Add(state);stack.Children.Add(mode);stack.Children.Add(hint);stack.Children.Add(arm);stack.Children.Add(combined);stack.Children.Add(fastSelf);
            var card=MenuTheme.Card(stack,new Thickness(14));card.Margin=new Thickness(0,9,0,0);card.BorderBrush=MenuTheme.Brush("#493A5D");body.Children.Add(card);
            refresh.Add(delegate
            {
                bool preview=data.dodger.mode=="PREVIEW";
                state.Text=data.dodger.pending?"DODGER · ОЖИДАЕТ ВКЛЮЧЕНИЯ":data.dodger.armed?"DODGER · ВКЛЮЧЁН":"DODGER · ВЫКЛЮЧЕН";
                state.Foreground=data.dodger.armed?MenuTheme.Green:MenuTheme.Purple;
                mode.Text=preview?"PREVIEW · КЛАВИШИ НЕ НАЖИМАЮТСЯ":"AUTO · АВТОМАТИЧЕСКАЯ ЗАЩИТА";
                mode.Foreground=preview?MenuTheme.Amber:MenuTheme.Green;hint.Text=data.dodger.mode_hint;
                arm.Content=data.dodger.armed||data.dodger.pending?"ВЫКЛЮЧИТЬ DODGER":preview?"ВКЛЮЧИТЬ НАБЛЮДЕНИЕ БЕЗ НАЖАТИЙ":"ВКЛЮЧИТЬ DODGER";
                arm.IsEnabled=connected&&(data.dodger.armed||data.dodger.pending||live&&data.dodger.available);
            });
            Section("СОСТОЯНИЕ");
            var status=MenuTheme.T("",11);status.TextWrapping=TextWrapping.Wrap;
            var eventText=MenuTheme.T("",9,MenuTheme.Muted);eventText.TextWrapping=TextWrapping.Wrap;eventText.Margin=new Thickness(0,5,0,0);
            var statusStack=new StackPanel();statusStack.Children.Add(status);statusStack.Children.Add(eventText);body.Children.Add(MenuTheme.Card(statusStack,new Thickness(12)));
            refresh.Add(delegate{status.Text=data.dodger.status;eventText.Text="Последнее решение: "+data.dodger.last_event;});
            Section("ЗАЩИТНЫЕ СПОСОБНОСТИ И ПРЕДМЕТЫ");
            if(rows.Count==0)Note("Выбери своего героя в Dota — здесь появятся его защиты и предметы.");
            foreach(SourceData row in rows)AddTile(row);
            AddSelectedDetail("Выбери защиту: покажем её механизм, состояние и ограничения.");
            var details=new Expander{Header="Как распознаётся угроза · диагностика",Foreground=MenuTheme.Muted,Margin=new Thickness(0,12,0,8),IsExpanded=false};
            var diagnostics=MenuTheme.T("",9,MenuTheme.Muted);diagnostics.TextWrapping=TextWrapping.Wrap;diagnostics.LineHeight=16;details.Content=diagnostics;body.Children.Add(details);
            refresh.Add(delegate{diagnostics.Text="Assassinate / первичный Shackleshot: оценка запуска. Близкий Assassinate: предзащита Counterspell по метке и фазе. Chaos Bolt: только диагностика, цель пока неизвестна.\n"+data.dodger.provider+"\n"+String.Join("\n",data.dodger.observations??new List<string>())+"\nВ консоли и killsteal.log ищи строки [DODGER]. При смене локального героя включи модуль заново.\nСвязь: "+diagnostic;});
            Note("Готовность означает наличие ресурсов, не гарантию dodge. Все источники имеют свои ограничения. Для теста сначала выбери Anti-Mage и включи Dodger; первая наблюдаемая стадия уже летящего выстрела не считается новым кастом.");
        }
        void HeroCard()
        {
            var g=new Grid{MinHeight=116};g.ColumnDefinitions.Add(new ColumnDefinition());g.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(176)});
            var left=new StackPanel{Margin=new Thickness(1,0,10,0)};left.Children.Add(MenuTheme.T("ЛОКАЛЬНЫЙ ГЕРОЙ  /  AUTO DETECT",8,MenuTheme.Purple,true));
            var name=MenuTheme.T("",26,null,true);name.Margin=new Thickness(0,8,0,6);left.Children.Add(name);
            var info=MenuTheme.T("",10,MenuTheme.Muted);left.Children.Add(info);
            var vitals=MenuTheme.T("",10,MenuTheme.Green);vitals.Margin=new Thickness(0,12,0,0);left.Children.Add(vitals);g.Children.Add(left);
            var art=new Image{Stretch=Stretch.UniformToFill,Opacity=.9};var artLayer=new Grid();var monogram=MenuTheme.T("❯",44,MenuTheme.Purple,true);monogram.HorizontalAlignment=HorizontalAlignment.Center;monogram.Opacity=.32;artLayer.Children.Add(monogram);artLayer.Children.Add(art);
            var artBorder=new Border{Child=artLayer,ClipToBounds=true,CornerRadius=new CornerRadius(8),Background=MenuTheme.Brush("#292137")};Grid.SetColumn(artBorder,1);g.Children.Add(artBorder);
            var card=MenuTheme.Card(g,new Thickness(15));card.Background=new LinearGradientBrush(Color.FromRgb(38,29,54),Color.FromRgb(23,24,34),0);body.Children.Add(card);
            refresh.Add(delegate{name.Text=String.IsNullOrEmpty(data.hero)?"Ожидаем героя":data.hero;info.Text=String.IsNullOrEmpty(data.hero)?"Запусти Dota 2 и дождись фида":"Уровень "+data.level+"   /   "+data.team+"   /   "+(data.alive?"В игре":"Мёртв");
                vitals.Text=!live?"HP / MP: данные не актуальны":"HP  "+MenuTheme.Number(data.hp)+" / "+MenuTheme.Number(data.maxhp)+"      MP  "+MenuTheme.Number(data.mana);art.Source=MenuTheme.HeroArt(data.hero_id);});
        }
        void KillstealArmCard()
        {
            var grid=new Grid{MinHeight=72};grid.ColumnDefinitions.Add(new ColumnDefinition());grid.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(196)});
            var text=new StackPanel{VerticalAlignment=VerticalAlignment.Center};var heading=MenuTheme.T("KILLSTEAL ВЫКЛЮЧЕН",10,MenuTheme.Purple,true);
            var detail=MenuTheme.T("Включение проверит фокус, живого героя и свежие данные.",9,MenuTheme.Muted);detail.TextWrapping=TextWrapping.Wrap;detail.Margin=new Thickness(0,5,8,0);text.Children.Add(heading);text.Children.Add(detail);grid.Children.Add(text);
            var arm=new Button{Content="ВКЛЮЧИТЬ KILLSTEAL",Margin=new Thickness(8,5,0,5),Padding=new Thickness(10,10,10,10),FontWeight=FontWeights.SemiBold,Background=MenuTheme.Brush("#3A2854"),BorderBrush=MenuTheme.Brush("#8963BF"),ToolTip="Безопасно закрыть меню и включить Killsteal после возврата фокуса в игру."};
            MenuTheme.AnimateButton(arm);arm.Click+=delegate{RequestArmOnClose();};Grid.SetColumn(arm,1);grid.Children.Add(arm);
            var card=MenuTheme.Card(grid,new Thickness(12));card.Margin=new Thickness(0,9,0,0);card.BorderBrush=MenuTheme.Brush("#493A5D");body.Children.Add(card);
            refresh.Add(delegate
            {
                heading.Text=data.armed?"KILLSTEAL ВКЛЮЧЁН":"KILLSTEAL ВЫКЛЮЧЕН";
                heading.Foreground=data.armed?MenuTheme.Green:MenuTheme.Purple;
                detail.Text=!connected?"Ожидается подключение локального фида.":!live?"Данные героя устарели; включение ждёт актуального состояния.":
                    data.mode=="PREVIEW"?"PREVIEW: состояние включится, но ввод в игру не отправляется.":
                    "После нажатия меню закроется; сервис дождётся фокуса, живого героя и свежих данных.";
                arm.IsEnabled=connected&&!String.IsNullOrEmpty(data.session);
                arm.Content=data.armed||data.arm_pending?"ВЫКЛЮЧИТЬ KILLSTEAL":data.mode=="PREVIEW"?"ВКЛЮЧИТЬ PREVIEW":"ВКЛЮЧИТЬ KILLSTEAL";
            });
        }
        void RequestArmOnClose()
        {
            if(connected&&(data.armed||data.arm_pending)){SendControls(null,false,null);return;}
            if(!connected||String.IsNullOrEmpty(data.session))
            {
                footer.Text="Killsteal не включён: сначала запусти start.bat и дождись подключения фида.";return;
            }
            suppressVisibilitySend=true;
            if(!SendControls(false,true)){suppressVisibilitySend=false;return;}
            footer.Text="Запрос принят интерфейсом: меню закрывается; включение ждёт фокус Dota и безопасное состояние героя.";
            Hide();
        }
        void Stats()
        {
            var grid=new Grid{Margin=new Thickness(0,10,0,0)};for(int i=0;i<3;i++)grid.ColumnDefinitions.Add(new ColumnDefinition());
            string[] captions={"ГОТОВЫЕ ИСТОЧНИКИ","СТАКИ FINGER","АВТОМАТИЗАЦИЯ"};var values=new List<TextBlock>();var subs=new List<TextBlock>();
            for(int i=0;i<3;i++){var p=new StackPanel();p.Children.Add(MenuTheme.T(captions[i],7,MenuTheme.Muted,true));var v=MenuTheme.T("—",19,i==0?MenuTheme.Green:MenuTheme.Purple,true);v.Margin=new Thickness(0,5,0,2);p.Children.Add(v);values.Add(v);var sub=MenuTheme.T("",8,MenuTheme.Muted);p.Children.Add(sub);subs.Add(sub);var c=MenuTheme.Card(p,new Thickness(11));c.Margin=new Thickness(0,0,i<2?8:0,0);Grid.SetColumn(c,i);grid.Children.Add(c);}
            body.Children.Add(grid);refresh.Add(delegate{List<SourceData> all=data.sources.Concat(data.items).Where(x=>x!=null&&x.supported).ToList();values[0].Text=(live?all.Count(x=>x.ready&&!desired.Contains(x.profile)):0)+" / "+all.Count;subs[0].Text="ресурсы / профиль";
                values[1].Text=live?data.stacks:"—";subs[1].Text="Талант: "+data.talent;subs[1].ToolTip=(data.stacks_reason??"")+"\n"+(data.talent_reason??"");
                values[2].Text=live&&data.armed?data.mode+" ON":"OFF";subs[2].Text="Кнопка в программе";});
        }
        void HeroesPage(List<SourceData> rows)
        {
            Section("ПОДДЕРЖИВАЕМЫЕ ГЕРОИ");
            if(data.catalog.Count==0){Note("Каталог загружается из профилей скрипта. Пока нет профилей способностей, список пуст.");return;}
            var wrap=new WrapPanel{Margin=new Thickness(-3,0,-3,0)};
            foreach(HeroCatalogData hero in data.catalog.Where(x=>x!=null))wrap.Children.Add(HeroButton(hero));
            var catalogCard=MenuTheme.Card(wrap,new Thickness(9));catalogCard.Margin=new Thickness(0,0,0,10);body.Children.Add(catalogCard);
            HeroCatalogData selected=SelectedHero();
            if(selected!=null)
            {
                var selectedHead=new StackPanel{Orientation=Orientation.Horizontal};var portrait=new Image{Width=43,Height=43,Stretch=Stretch.UniformToFill,Source=MenuTheme.HeroIcon(selected.hero_id)};
                var frame=new Border{Width=47,Height=47,CornerRadius=new CornerRadius(7),Background=MenuTheme.Brush("#2A2237"),Child=portrait,ClipToBounds=true};selectedHead.Children.Add(frame);
                var text=new StackPanel{Margin=new Thickness(11,0,0,0),VerticalAlignment=VerticalAlignment.Center};text.Children.Add(MenuTheme.T(selected.hero,17,null,true));text.Children.Add(MenuTheme.T(rows.Count+" поддерживаемых способностей",9,MenuTheme.Muted));selectedHead.Children.Add(text);
                var heroHead=MenuTheme.Card(selectedHead,new Thickness(11));heroHead.Margin=new Thickness(0,0,0,9);body.Children.Add(heroHead);
                Section("СПОСОБНОСТИ ИЗ КАТАЛОГА РАЗРАБОТЧИКА");
                foreach(SourceData row in rows)AddTile(row);
                AddSelectedDetail("Нажми на способность, чтобы увидеть её имя и профиль. Здесь можно только включить/выключить поддержку в текущей сессии.");
            }
            Note("В списке только герои с активными профилями скрипта. Выбор здесь настраивает профили, но не меняет героя в игре: runtime применяет способность только при совпадении с фактическим локальным героем.");
        }
        Border HeroButton(HeroCatalogData hero)
        {
            bool selected=hero.id==selectedHeroId;var panel=new StackPanel{Width=70};
            var image=new Image{Width=48,Height=48,Stretch=Stretch.UniformToFill,Source=MenuTheme.HeroIcon(hero.hero_id)};
            var frame=new Border{Width=50,Height=50,CornerRadius=new CornerRadius(7),Background=MenuTheme.Brush("#272333"),BorderBrush=MenuTheme.Brush(selected?"#A887FF":"#3B3946"),BorderThickness=new Thickness(selected?2:1),Child=image,ClipToBounds=true,HorizontalAlignment=HorizontalAlignment.Center};panel.Children.Add(frame);
            var name=MenuTheme.T(hero.hero,8,selected?MenuTheme.Purple:MenuTheme.Muted,selected);name.HorizontalAlignment=HorizontalAlignment.Center;name.Margin=new Thickness(0,5,0,0);panel.Children.Add(name);
            var card=new Border{Width=77,Height=76,Margin=new Thickness(3),Padding=new Thickness(3),CornerRadius=new CornerRadius(8),Background=MenuTheme.Brush(selected?"#282137":"#171820"),BorderBrush=MenuTheme.Brush(selected?"#70549D":"#2B2C3A"),BorderThickness=new Thickness(1),Child=panel,Cursor=Cursors.Hand,ToolTip=hero.hero};
            MenuTheme.AnimateElement(card);card.MouseLeftButtonUp+=delegate{selectedHeroId=hero.id;selectedId="";Rebuild(true);};return card;
        }
        void AddTile(SourceData row)
        {
            var tile=new SourceTile(Change,Select);tile.Set(row,live,connected,row.defensive?dodgeDesired:desired,row.id==selectedId);tiles.Add(tile);body.Children.Add(tile);
        }
        void AddSelectedDetail(string emptyText)
        {
            selectedDetail=MenuTheme.T("",10,MenuTheme.Muted);selectedDetail.TextWrapping=TextWrapping.Wrap;selectedDetail.LineHeight=17;
            var detail=MenuTheme.Card(selectedDetail,new Thickness(12));detail.Margin=new Thickness(0,4,0,9);body.Children.Add(detail);
            if(selectedId=="")selectedDetail.Text=emptyText;
        }
        void Select(SourceData row){selectedId=row.id;for(int i=0;i<tiles.Count&&i<shown.Count;i++)tiles[i].Set(shown[i],live,connected,page==0&&scriptModule=="Dodger"?dodgeDesired:desired,shown[i].id==selectedId);UpdateSelected();}
        SourceData FindRow(string id)
        {
            if(String.IsNullOrEmpty(id))return null;
            if(page==0&&scriptModule=="Dodger")return Rows().FirstOrDefault(x=>x.id==id);
            SourceData row=data.sources.Concat(data.items).FirstOrDefault(x=>x!=null&&x.id==id);
            if(row!=null)return row;
            foreach(HeroCatalogData hero in data.catalog){if(hero==null||hero.abilities==null)continue;row=hero.abilities.FirstOrDefault(x=>x!=null&&x.id==id);if(row!=null)return row;}
            return null;
        }
        void UpdateSelected()
        {
            if(selectedDetail==null)return;SourceData row=FindRow(selectedId);
            if(row==null){selectedDetail.Text=page==1?"Выбери способность, чтобы увидеть сведения о профиле.":"Нажми на источник для текущего состояния, модели и причины блокировки.";return;}
            if(row.defensive){selectedDetail.Text=(row.title??row.name)+"  ·  "+row.damage_type+"\n"+(row.reason??"")+"\nКлавиша: "+row.key+" · "+MenuTheme.StateName(row.status);return;}
            if(row.catalog){selectedDetail.Text=(row.title??row.name)+"  /  "+row.name+"\n"+(row.reason??"Профиль из каталога разработчика.")+"\nПоддержка runtime всё равно сверяется с фактическим локальным героем.";return;}
            selectedDetail.Text=(row.title??row.name)+"  /  "+row.name+"\n"+(row.reason??"")+(String.IsNullOrEmpty(row.damage_note)?"":"\n"+row.damage_note)+"\n"+
                (row.supported?"Тип: "+row.damage_type+" · Каст: "+row.cast+" · Дальность: "+row.range+" (профиль) · Урон до защиты: "+(row.damage.HasValue?MenuTheme.Number(row.damage.Value):"нет данных")+". Усиление предметами не учтено.":"Источник не поддерживается.");
        }
        void Change(SourceData row,bool on)
        {
            if(syncing||!connected||row==null||!row.supported||String.IsNullOrEmpty(data.session))return;
            HashSet<string> flags=row.defensive?dodgeDesired:desired;
            if(on)flags.Remove(row.profile);else flags.Add(row.profile);SendControls();
        }
        bool SendControls(bool? pauseOverride=null,bool? armAfterClose=null,bool? dodgeArm=null)
        {
            if(String.IsNullOrEmpty(session))return false;
            int revision=Math.Max(pendingRevision,data.revision)+1;
            try
            {
                var doc=new Dictionary<string,object>{{"session",data.session},{"revision",revision},{"disabled",desired.ToArray()},{"menu_open",pauseOverride??IsVisible}};
                doc["preserve_armed"]=true;doc["dodge_alt_selfcast"]=altSelfDesired;
                doc["menu_hwnd"]=new System.Windows.Interop.WindowInteropHelper(this).Handle.ToInt64();
                doc["menu_pid"]=Process.GetCurrentProcess().Id;
                if(armAfterClose.HasValue)doc["arm_after_close"]=armAfterClose.Value;
                if(dodgeArm.HasValue)doc["dodge_arm"]=dodgeArm.Value;doc["dodge_disabled"]=dodgeDesired.ToArray();
                string text=new JavaScriptSerializer().Serialize(doc);string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"ui_session_controls.json");string tmp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
                try{File.WriteAllText(tmp,text,new UTF8Encoding(false));if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);}finally{if(File.Exists(tmp))File.Delete(tmp);}
                pendingRevision=revision;error="";footer.Text=armAfterClose==true||dodgeArm==true?"Запрос включения отправлен; ждём фокуса и свежих данных.":"Настройки сохранены. Меню приостанавливает ввод, но не выключает модули.";
                return true;
            }
            catch(Exception ex){desired=new HashSet<string>(data.disabled);dodgeDesired=new HashSet<string>(data.dodger.disabled);error="Не удалось записать настройку: "+ex.Message;OverlayLog.Write(error);footer.Text=error;return false;}
        }

        void VisualPage()
        {
            Section("HUD-ЭЛЕМЕНТЫ","hud");
            VisualSwitch("Оверлей HP и способностей над героями",()=>overlay.platesEnabled,x=>overlay.platesEnabled=x,"Отдельный переключатель: плашки следуют за героями только внутри окна Dota и не зависят от нижней панели. F7 — локальный герой, F8 — фильтр врагов.");
            VisualSwitch("Показывать локального героя · F7",()=>overlay.showLocal,x=>overlay.showLocal=x,null);
            VisualSwitch("Только враги · F8",()=>overlay.enemiesOnly,x=>overlay.enemiesOnly=x,null);
            VisualSwitch("Компактный статус HACHBOB",()=>overlay.compactEnabled,x=>overlay.compactEnabled=x,null);
            VisualSwitch("Нижний ряд иконок героев",()=>overlay.visual.heroStripEnabled,x=>overlay.visual.heroStripEnabled=x,null,"hero-row");
            VisualSwitch("Нижняя панель HP и способностей · до 5 врагов",()=>overlay.visual.enemyBadgeEnabled,x=>overlay.visual.enemyBadgeEnabled=x,
                "Работает независимо от оверлея над героями. Только текущие видимые враги; FOW/last-seen не кэшируются.","enemy-badge");
            VisualSwitch("Режим размещения · перетаскивание",()=>overlay.visual.layoutEdit,x=>overlay.visual.layoutEdit=x,"Включи режим, сверни меню клавишей Insert и перетаскивай HUD в игре.","placement");
            Section("РАЗМЕР И ПРОЗРАЧНОСТЬ");
            VisualSlider("Размер HUD",()=>overlay.visual.size,v=>overlay.visual.size=v,.65,1.65,true,"0.00×");
            VisualSlider("Прозрачность",()=>overlay.visual.opacity,v=>overlay.visual.opacity=v,.25,1,true,"fraction%");
            Section("ПОЗИЦИЯ · ОТНОСИТЕЛЬНО ОКНА DOTA 2");
            VisualSlider("Ряд героев · центр X",()=>overlay.visual.heroStripX*100,v=>overlay.visual.heroStripX=v/100,0,100,true,"0%");
            VisualSlider("Ряд героев · верх Y",()=>overlay.visual.heroStripY*100,v=>overlay.visual.heroStripY=v/100,0,100,true,"0%");
            VisualSlider("HP HUD · центр X",()=>overlay.visual.enemyBadgeX*100,v=>overlay.visual.enemyBadgeX=v/100,0,100,true,"0%");
            VisualSlider("HP HUD · верх Y",()=>overlay.visual.enemyBadgeY*100,v=>overlay.visual.enemyBadgeY=v/100,0,100,true,"0%");
            var actions=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,6,0,0)};
            var reset=new Button{Content="Сбросить размещение",Margin=new Thickness(0,0,8,0)};MenuTheme.AnimateButton(reset);reset.Click+=delegate{overlay.ResetPlacement();RefreshVisualControls();footer.Text="HUD возвращён вниз по центру. Остальные визуальные настройки сохранены.";};actions.Children.Add(reset);
            body.Children.Add(actions);
            Note("Оверлей над героями и нижняя панель независимы и могут работать одновременно. Плашки над героями следуют за экранной позицией только внутри клиентской области Dota; панель остаётся фиксированной относительно окна, а FOW/last-seen враги не показываются. Это стилизованный WPF-оверлей, не встройка в клиент. Режим размещения двигает нижнюю панель и ряд портретов; настройки сохраняются в overlay_visual.json.");
        }
        void VisualSwitch(string label,Func<bool> read,Action<bool> write,string detail,string anchor=null)
        {
            var stack=new StackPanel();var c=new CheckBox{Content=label,IsChecked=read(),Margin=new Thickness(0,0,0,String.IsNullOrEmpty(detail)?0:4)};
            MenuTheme.AnimateElement(c);stack.Children.Add(c);
            if(!String.IsNullOrEmpty(detail)){var d=MenuTheme.T(detail,9,MenuTheme.Muted);d.TextWrapping=TextWrapping.Wrap;stack.Children.Add(d);}
            var card=MenuTheme.Card(stack,new Thickness(12));card.Margin=new Thickness(0,0,0,6);body.Children.Add(card);RegisterAnchor(anchor,card);
            c.Checked+=delegate{if(!syncing){write(true);overlay.SaveVisualSettings();}};c.Unchecked+=delegate{if(!syncing){write(false);overlay.SaveVisualSettings();}};
            refresh.Add(delegate{bool value=read();if(c.IsChecked!=value)c.IsChecked=value;});
        }
        void VisualSlider(string label,Func<double> read,Action<double> write,double min,double max,bool persist,string format)
        {
            var grid=new Grid();grid.RowDefinitions.Add(new RowDefinition{Height=GridLength.Auto});grid.RowDefinitions.Add(new RowDefinition{Height=GridLength.Auto});grid.ColumnDefinitions.Add(new ColumnDefinition());grid.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(62)});
            var name=MenuTheme.T(label,10,MenuTheme.Text,true);grid.Children.Add(name);var value=MenuTheme.T("",9,MenuTheme.Purple,true);value.HorizontalAlignment=HorizontalAlignment.Right;Grid.SetColumn(value,1);grid.Children.Add(value);
            var slider=new Slider{Minimum=min,Maximum=max,SmallChange=1,LargeChange=Math.Max(1,(max-min)/10),TickFrequency=Math.Max(1,(max-min)/10),IsSnapToTickEnabled=false,Foreground=MenuTheme.Purple,Margin=new Thickness(0,6,0,0),Value=Math.Max(min,Math.Min(max,read()))};Grid.SetRow(slider,1);Grid.SetColumnSpan(slider,2);grid.Children.Add(slider);
            var card=MenuTheme.Card(grid,new Thickness(11));card.Margin=new Thickness(0,0,0,6);body.Children.Add(card);
            Action updateLabel=delegate{double v=slider.Value;string n=v.ToString("0",CultureInfo.InvariantCulture);if(format=="0.00×")n=v.ToString("0.00",CultureInfo.InvariantCulture);else if(format=="fraction%")n=Math.Round(v*100).ToString("0",CultureInfo.InvariantCulture);value.Text=format=="fraction%"||format=="0%"?n+"%":format=="0 px"?n+" px":format=="0.00×"?n+"×":n;};
            slider.ValueChanged+=delegate{updateLabel();if(!syncing){write(slider.Value);if(persist)overlay.SaveVisualSettings();overlay.InvalidateVisual();}};
            refresh.Add(delegate{double v=Math.Max(min,Math.Min(max,read()));if(Math.Abs(slider.Value-v)>.01)slider.Value=v;updateLabel();});updateLabel();
        }
        void RefreshVisualControls(){syncing=true;foreach(Action a in refresh)a();syncing=false;overlay.InvalidateVisual();}
        void SettingsPage()
        {
            Section("ИМПОРТ OFFSETS / ОБНОВЛЕНИЕ","offsets");
            Note("Выбери client_dll.json и offsets.json из одного комплекта (оба обязательны). Можно также добавить buttons.json / info.json или выбрать ZIP. Файл ks_profiles.json через меню не импортируется: каталог способностей обновляет разработчик.");
            var patchActions=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,4,0,7)};
            var patch=new Button{Content="Импорт offsets…",Margin=new Thickness(0,0,8,0)};MenuTheme.AnimateButton(patch);patch.Click+=delegate{StagePatchPackage();};patchActions.Children.Add(patch);
            var discard=new Button{Content="Отменить ожидающий импорт"};MenuTheme.AnimateButton(discard);discard.Click+=delegate{DiscardStagedPatch();};patchActions.Children.Add(discard);body.Children.Add(patchActions);
            var status=MenuTheme.T("",10,MenuTheme.Muted);status.TextWrapping=TextWrapping.Wrap;status.LineHeight=17;var statusCard=MenuTheme.Card(status,new Thickness(12));statusCard.Margin=new Thickness(0,0,0,10);body.Children.Add(statusCard);
            refresh.Add(delegate{status.Text="Состояние reader: "+(connected?"подключён":"ожидание UDP на 127.0.0.1:27971")+"\nДанные: "+(live?"актуальны":"не актуальны")+" · AUTO: "+(data.armed?"ON":"OFF")+" · Управление: кнопка программы\n"+data.status;});
            Section("ОБЩЕЕ");Note("Импорт только ставит проверенный комплект offsets в очередь. Применение выполняется при следующем чистом запуске start.bat; совместимость со сборкой игры автоматически не подтверждается. После импорта AUTO остаётся выключенным.");
            Section("ЖУРНАЛЫ","logs");
            var logs=new StackPanel{Orientation=Orientation.Horizontal};foreach(string f in new[]{"killsteal.log","overlay.log"}){string file=f;var b=new Button{Content=file,Margin=new Thickness(0,3,8,0)};MenuTheme.AnimateButton(b);b.Click+=delegate{OpenFile(file);};logs.Children.Add(b);}body.Children.Add(logs);
            Note("Insert открывает и закрывает меню без выключения модулей. Пока меню открыто, ввод приостановлен; после закрытия нужны фокус Dota и свежие данные. Кнопки страниц включают/выключают модули явно. F6 не переключает Killsteal.");
        }
        void OpenFile(string file)
        {
            try{Process.Start("notepad.exe","\""+Path.Combine(AppDomain.CurrentDomain.BaseDirectory,file)+"\"");}
            catch(Exception ex){error="Не удалось открыть файл: "+ex.Message;footer.Text=error;}
        }
        string RunPythonCheck(string script,string arguments)
        {
            string root=AppDomain.CurrentDomain.BaseDirectory;var psi=new ProcessStartInfo("py.exe","-3.11 \""+Path.Combine(root,script)+"\" "+arguments);
            psi.WorkingDirectory=root;psi.UseShellExecute=false;psi.CreateNoWindow=true;psi.RedirectStandardOutput=true;psi.RedirectStandardError=true;psi.EnvironmentVariables["PYTHONUTF8"]="1";
            using(var p=Process.Start(psi)){string stdout=p.StandardOutput.ReadToEnd();string stderr=p.StandardError.ReadToEnd();p.WaitForExit();return "Exit code: "+p.ExitCode+"\n"+stdout+(String.IsNullOrWhiteSpace(stderr)?"":"\nSTDERR:\n"+stderr);}
        }
        void StagePatchPackage()
        {
            string listFile=null;
            try
            {
                var d=new Microsoft.Win32.OpenFileDialog{Title="Выбери комплект JSON/ZIP: client_dll.json + offsets.json обязательны",Filter="JSON/ZIP package (*.json;*.zip)|*.json;*.zip|All files (*.*)|*.*",CheckFileExists=true,Multiselect=true};
                if(d.ShowDialog(this)!=true||d.FileNames.Length==0)return;
                listFile=Path.Combine(Path.GetTempPath(),"hachbob-patch-files-"+Guid.NewGuid().ToString("N")+".txt");File.WriteAllLines(listFile,d.FileNames,new UTF8Encoding(false));
                string result=RunPythonCheck("patch_bundle.py","--stage-list-user \""+listFile+"\"");bool ok=result.Contains("Exit code: 0");
                MessageBox.Show(this,result,"Проверка/импорт offsets",MessageBoxButton.OK,ok?MessageBoxImage.Information:MessageBoxImage.Warning);
            }
            catch(Exception ex){error="Импорт JSON не выполнен: "+ex.Message;footer.Text=error;MessageBox.Show(this,error,"Настройки",MessageBoxButton.OK,MessageBoxImage.Warning);}
            finally{if(!String.IsNullOrEmpty(listFile)&&File.Exists(listFile))try{File.Delete(listFile);}catch{}}
        }
        void DiscardStagedPatch()
        {
            try{string result=RunPythonCheck("patch_bundle.py","--discard-staged");MessageBox.Show(this,result,"Ожидающий импорт",MessageBoxButton.OK,result.Contains("Exit code: 0")?MessageBoxImage.Information:MessageBoxImage.Warning);}
            catch(Exception ex){error="Не удалось отменить импорт: "+ex.Message;footer.Text=error;MessageBox.Show(this,error,"Настройки",MessageBoxButton.OK,MessageBoxImage.Warning);}
        }
        void RegisterAnchor(string key,FrameworkElement element)
        {
            if(!String.IsNullOrEmpty(key)&&element!=null)anchors[key]=element;
        }
        void ScrollToAnchor(string key)
        {
            FrameworkElement target;
            if(!anchors.TryGetValue(key,out target)||target==null)
            {
                scroll.ScrollToTop();footer.Text="Раздел не найден в текущей странице.";return;
            }
            target.BringIntoView();
            footer.Text="Переход к разделу интерфейса: "+key+".";
        }
        void Section(string text,string anchor=null)
        {
            var t=MenuTheme.T(text,9,MenuTheme.Muted,true);t.Margin=new Thickness(0,12,0,9);body.Children.Add(t);RegisterAnchor(anchor,t);
        }
        void Note(string text){var t=MenuTheme.T(text,10,MenuTheme.Muted);t.TextWrapping=TextWrapping.Wrap;t.Margin=new Thickness(0,3,0,9);t.LineHeight=17;body.Children.Add(t);}
    }
}
