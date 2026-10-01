// Native camera page / MPL-2.0. WPF controls only: no game input or memory IO.
using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Threading;

namespace EspOverlay
{
    // Separate draft, immutable sent command, and acknowledged state. Heartbeats
    // must not submit intermediate drag values or mutate an existing revision.
    internal sealed class ZoomIntent
    {
        public int Revision,Distance,SentDistance;
        public bool Haze,SentHaze,Pending,Awaiting;
        public void Reset(){Revision=Distance=SentDistance=0;Haze=SentHaze=Pending=Awaiting=false;}
        public void ResetAfter(int acknowledged){Reset();Revision=acknowledged>=0&&acknowledged<int.MaxValue?acknowledged+1:0;Awaiting=true;}
        public void StageDistance(int value){Distance=value<=975?0:Math.Max(1000,Math.Min(2200,25*(int)Math.Round(value/25.0)));Pending=true;}
        public void ToggleHaze(){Haze=!Haze;Pending=true;}
        public bool Commit()
        {
            if(!Pending)return false;
            Revision++;SentDistance=Distance;SentHaze=Haze;Pending=false;Awaiting=true;return true;
        }
        public void Acknowledge(int revision,bool busy,int distance,bool haze)
        {
            if(revision!=Revision||busy)return;
            Awaiting=false;
            if(!Pending){Distance=distance;Haze=haze;}
            // A failed request can acknowledge OFF. Its same-revision heartbeat
            // still describes the original command, never a silent new request.
        }
        public void Stop(){Distance=0;Haze=false;Pending=true;Commit();}
    }

    partial class MenuWindow
    {
        readonly ZoomIntent zoomIntent=new ZoomIntent();
        DispatcherTimer zoomTimer,zoomDebounce;
        string zoomSession="",zoomError="",zoomLoggedError="";
        bool zoomDragging;
        void InitializeZoom()
        {
            zoomTimer=new DispatcherTimer{Interval=TimeSpan.FromMilliseconds(400)};
            zoomTimer.Tick+=delegate{WriteZoom();};zoomTimer.Start();
            zoomDebounce=new DispatcherTimer{Interval=TimeSpan.FromMilliseconds(180)};
            zoomDebounce.Tick+=delegate{if(!zoomDragging)CommitZoom();};
        }
        void SyncZoom()
        {
            ZoomData z=data.zoom??new ZoomData();
            if(zoomSession!=session)
            {
                zoomSession=session;zoomIntent.ResetAfter(z.revision);zoomError=zoomLoggedError="";zoomDragging=false;zoomDebounce.Stop();
            }
            zoomIntent.Acknowledge(z.revision,z.busy,z.distance,z.haze);
            string failure=(z.error??"")+" "+(z.restore_error??"");
            string errorKey=z.revision+"|"+failure;
            if(failure.Trim().Length>0&&errorKey!=zoomLoggedError)
            {
                zoomLoggedError=errorKey;OverlayLog.Write("CAMERA revision="+z.revision+" "+failure);
            }
        }
        void CommitZoom()
        {
            zoomDebounce.Stop();
            if(zoomIntent.Commit()){zoomError="";WriteZoom();}
        }
        void WriteZoom()
        {
            if(String.IsNullOrEmpty(session)||!connected)return;
            string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"zoom_controls.json");
            string tmp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
            try
            {
                var doc=new {version=1,session=session,revision=zoomIntent.Revision,
                    utc_ms=(long)(DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalMilliseconds,
                    distance=zoomIntent.SentDistance,haze=zoomIntent.SentHaze};
                File.WriteAllText(tmp,new JavaScriptSerializer().Serialize(doc),new UTF8Encoding(false));
                for(int i=0;;i++)
                {
                    try{if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);break;}
                    catch(IOException){if(i>=2)throw;Thread.Sleep(2);}
                }
                zoomError="";
            }
            catch(Exception ex)
            {
                string message="Не удалось передать настройку: "+ex.Message;
                if(zoomError!=message)OverlayLog.Write("CAMERA IPC "+message);
                zoomError=message;
            }
            finally{try{if(File.Exists(tmp))File.Delete(tmp);}catch{}}
        }
        void FinishZoom()
        {
            zoomIntent.Stop();WriteZoom();zoomDragging=false;
            if(zoomTimer!=null)zoomTimer.Stop();if(zoomDebounce!=null)zoomDebounce.Stop();
        }

        // Native WPF templates, all assets inline. Track/Thumb retain keyboard,
        // click-to-position and drag behaviour; no hand-written mouse sender.
        const string ZoomSliderXaml=@"<ControlTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml' TargetType='{x:Type Slider}'>
<Grid x:Name='Root' Height='36' Background='Transparent'>
<Border Height='6' CornerRadius='3' Background='#2D2F43' VerticalAlignment='Center' Margin='10,0'/>
<Track x:Name='PART_Track' Minimum='{TemplateBinding Minimum}' Maximum='{TemplateBinding Maximum}' Value='{TemplateBinding Value}' IsDirectionReversed='{TemplateBinding IsDirectionReversed}'>
<Track.DecreaseRepeatButton><RepeatButton Command='{x:Static Slider.DecreaseLarge}' Focusable='False'>
<RepeatButton.Template><ControlTemplate TargetType='{x:Type RepeatButton}'><Border Height='6' CornerRadius='3' VerticalAlignment='Center'><Border.Background><LinearGradientBrush StartPoint='0,0' EndPoint='1,0'><GradientStop Color='#7554DA' Offset='0'/><GradientStop Color='#C3A4FF' Offset='1'/></LinearGradientBrush></Border.Background></Border></ControlTemplate></RepeatButton.Template>
</RepeatButton></Track.DecreaseRepeatButton>
<Track.IncreaseRepeatButton><RepeatButton Command='{x:Static Slider.IncreaseLarge}' Focusable='False'>
<RepeatButton.Template><ControlTemplate TargetType='{x:Type RepeatButton}'><Border Height='6' CornerRadius='3' Background='#2D2F43' VerticalAlignment='Center'/></ControlTemplate></RepeatButton.Template>
</RepeatButton></Track.IncreaseRepeatButton>
<Track.Thumb><Thumb Width='26' Height='26' Focusable='False'>
<Thumb.Template><ControlTemplate TargetType='{x:Type Thumb}'><Grid><Ellipse x:Name='Halo' Fill='#A98BE8' Opacity='0.22'/><Ellipse x:Name='Knob' Margin='5' Fill='#F2EBFF' Stroke='#C3A4FF' StrokeThickness='2'/></Grid><ControlTemplate.Triggers><Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Halo' Property='Opacity' Value='0.5'/></Trigger><Trigger Property='IsDragging' Value='True'><Setter TargetName='Halo' Property='Opacity' Value='0.65'/><Setter TargetName='Knob' Property='Fill' Value='White'/></Trigger></ControlTemplate.Triggers></ControlTemplate></Thumb.Template>
</Thumb></Track.Thumb></Track>
</Grid><ControlTemplate.Triggers><Trigger Property='IsEnabled' Value='False'><Setter TargetName='Root' Property='Opacity' Value='0.35'/></Trigger><Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='Root' Property='Background' Value='#10101022'/></Trigger></ControlTemplate.Triggers></ControlTemplate>";
        const string ZoomButtonXaml=@"<ControlTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml' TargetType='{x:Type Button}'>
<Border x:Name='Surface' CornerRadius='9' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='1' Padding='{TemplateBinding Padding}'><ContentPresenter HorizontalAlignment='Center' VerticalAlignment='Center'/></Border>
<ControlTemplate.Triggers><Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Surface' Property='BorderBrush' Value='#BAA1ED'/></Trigger><Trigger Property='IsPressed' Value='True'><Setter TargetName='Surface' Property='Opacity' Value='0.7'/></Trigger><Trigger Property='IsEnabled' Value='False'><Setter TargetName='Surface' Property='Opacity' Value='0.4'/></Trigger><Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='Surface' Property='BorderBrush' Value='#E3D6FF'/></Trigger></ControlTemplate.Triggers></ControlTemplate>";

        static void TryZoomTemplate(Control control,string xaml)
        {
            try{control.Template=(ControlTemplate)XamlReader.Parse(xaml);}
            catch(Exception ex){OverlayLog.Write("CAMERA TEMPLATE (using default): "+ex.Message);}
        }
        void ZoomPage()
        {
            var panel=new StackPanel();
            var header=new Grid();
            header.Children.Add(MenuTheme.T("ОБЗОР КАРТЫ",10,MenuTheme.Purple,true));
            var badgeText=MenuTheme.T("АВТО",10,MenuTheme.Muted,true);
            var badge=new Border{Child=badgeText,CornerRadius=new CornerRadius(7),Padding=new Thickness(10,5,10,5),HorizontalAlignment=HorizontalAlignment.Right,Background=MenuTheme.Brush("#242532")};
            header.Children.Add(badge);panel.Children.Add(header);
            var title=MenuTheme.T("Дистанция камеры",20,null,true);title.Margin=new Thickness(0,18,0,4);panel.Children.Add(title);
            panel.Children.Add(MenuTheme.T("ZFar включается автоматически вместе с зумом",11,MenuTheme.Muted));
            var valueRow=new Grid{Margin=new Thickness(0,22,0,8)};
            var valueLabel=MenuTheme.T("Авто",34,MenuTheme.Text,true);valueRow.Children.Add(valueLabel);
            var actual=MenuTheme.T("Камера игры",11,MenuTheme.Muted);actual.HorizontalAlignment=HorizontalAlignment.Right;actual.VerticalAlignment=VerticalAlignment.Bottom;actual.Margin=new Thickness(0,0,0,6);valueRow.Children.Add(actual);panel.Children.Add(valueRow);
            var slider=new Slider{Minimum=975,Maximum=2200,TickFrequency=25,IsSnapToTickEnabled=true,IsMoveToPointEnabled=true,
                Value=zoomIntent.Distance==0?975:zoomIntent.Distance,SmallChange=25,LargeChange=100,Height=36,
                HorizontalAlignment=HorizontalAlignment.Stretch};
            TryZoomTemplate(slider,ZoomSliderXaml);
            panel.Children.Add(slider);
            var range=new Grid{Margin=new Thickness(6,2,6,0)};
            range.Children.Add(MenuTheme.T("Авто",10,MenuTheme.Muted));
            var middle=MenuTheme.T("1600",10,MenuTheme.Muted);middle.HorizontalAlignment=HorizontalAlignment.Center;range.Children.Add(middle);
            var right=MenuTheme.T("2200",10,MenuTheme.Muted);right.HorizontalAlignment=HorizontalAlignment.Right;range.Children.Add(right);panel.Children.Add(range);
            var tip=MenuTheme.T("Влево до упора — вернуть исходную камеру. Отпусти ползунок, чтобы применить дистанцию.",10,MenuTheme.Muted);tip.TextWrapping=TextWrapping.Wrap;tip.Margin=new Thickness(0,14,0,20);panel.Children.Add(tip);
            panel.Children.Add(new Border{Height=1,Background=MenuTheme.Brush("#2B2D3D"),Margin=new Thickness(0,0,0,18)});
            var hazeRow=new Grid();hazeRow.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(1,GridUnitType.Star)});hazeRow.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
            var hazeText=new StackPanel{VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(0,0,16,0)};
            hazeText.Children.Add(MenuTheme.T("Атмосферная дымка",13,null,true));
            var hazeNote=MenuTheme.T("Обычная атмосфера игры",10,MenuTheme.Muted);hazeNote.Margin=new Thickness(0,4,0,0);hazeText.Children.Add(hazeNote);hazeRow.Children.Add(hazeText);
            var haze=new Button{Content="Отключить дымку",MinWidth=174,FontSize=11,Padding=new Thickness(14,12,14,12),
                BorderBrush=MenuTheme.Brush("#534469"),Background=MenuTheme.Brush("#292239"),Foreground=MenuTheme.Text};
            TryZoomTemplate(haze,ZoomButtonXaml);
            Grid.SetColumn(haze,1);hazeRow.Children.Add(haze);panel.Children.Add(hazeRow);
            var status=MenuTheme.T("",11,MenuTheme.Muted);status.TextWrapping=TextWrapping.Wrap;status.TextTrimming=TextTrimming.None;status.LineHeight=17;
            var statusBox=new Border{Child=status,Padding=new Thickness(12),Margin=new Thickness(0,20,0,0),CornerRadius=new CornerRadius(8),Background=MenuTheme.Brush("#11131B")};panel.Children.Add(statusBox);
            slider.ValueChanged+=delegate
            {
                if(syncing)return;
                zoomIntent.StageDistance((int)Math.Round(slider.Value));zoomError="";
                valueLabel.Text=zoomIntent.Distance==0?"Авто":zoomIntent.Distance.ToString();
                zoomDebounce.Stop();zoomDebounce.Start();
            };
            slider.AddHandler(Thumb.DragStartedEvent,new DragStartedEventHandler(delegate{zoomDragging=true;}));
            slider.AddHandler(Thumb.DragCompletedEvent,new DragCompletedEventHandler(delegate{zoomDragging=false;CommitZoom();}));
            slider.PreviewMouseLeftButtonUp+=delegate{if(!zoomDragging)CommitZoom();};
            haze.Click+=delegate{if(syncing)return;zoomIntent.ToggleHaze();CommitZoom();};
            refresh.Add(delegate
            {
                ZoomData z=data.zoom??new ZoomData();
                string errorText=z.error??"",restore=z.restore_error??"";
                bool pending=zoomIntent.Pending||zoomIntent.Awaiting||z.busy;
                bool bad=errorText.Length>0||restore.Length>0||zoomError.Length>0;
                valueLabel.Text=zoomIntent.Distance==0?"Авто":zoomIntent.Distance.ToString();
                if(!slider.IsMouseCaptureWithin&&!zoomIntent.Pending)slider.Value=zoomIntent.Distance==0?975:zoomIntent.Distance;
                actual.Text=z.distance>0?"В игре: "+z.distance+"  ·  ZFar":"В игре: исходная камера";
                slider.IsEnabled=connected&&z.available&&!z.preview;haze.IsEnabled=slider.IsEnabled;
                haze.Content=zoomIntent.Haze?"Вернуть дымку":"Отключить дымку";
                haze.Background=MenuTheme.Brush(zoomIntent.Haze?"#19322F":"#292239");haze.Foreground=zoomIntent.Haze?MenuTheme.Green:MenuTheme.Text;
                hazeNote.Text=z.haze?"Дымка отключена":"Обычная атмосфера игры";
                badgeText.Text=!connected?"НЕТ СВЯЗИ":bad&&!pending?"НЕ ПРИМЕНЕНО":pending?"НАСТРОЙКА":z.distance>0||z.haze?"АКТИВНО":"АВТО";
                badgeText.Foreground=bad&&!pending?MenuTheme.Amber:pending?MenuTheme.Purple:z.distance>0||z.haze?MenuTheme.Green:MenuTheme.Muted;
                string text=!connected?"Нет связи с HACHBOB. Камера не включается без свежих данных.":
                    zoomError.Length>0?zoomError:zoomIntent.Pending?"Отпусти ползунок — применится выбранное значение.":
                    z.busy?(z.status??"Применяю настройки…"):zoomIntent.Awaiting?"Ожидаю подтверждение от модуля камеры…":
                    errorText.Length>0?errorText:!z.available?"Модуль камеры недоступен. Перезапусти обновлённый start.bat.":z.status??"Авто · камера игры";
                if(errorText.Contains("Сборка")||errorText.Contains("Код расчёта")||errorText.Contains("метод обновления"))text+="\nПосле патча нужен проверенный профиль камеры, не только новые offsets.";
                if(restore.Length>0)text+="\nВозврат не подтверждён: "+restore;
                status.Text=text;status.Foreground=bad?MenuTheme.Amber:MenuTheme.Muted;
            });
            body.Children.Add(MenuTheme.Card(panel,new Thickness(24)));
            Note("Insert — скрыть меню, сохранив настройки. End — возврат и выход. Туман войны не меняется.");
        }
    }
}
