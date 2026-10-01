// Native Last Hit page. Menu writes commands only; the existing Python sender acts.
using System;
using System.IO;
using System.Collections.Generic;
using System.Globalization;
using System.Windows.Media;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;

namespace EspOverlay
{
    public class LastHitPreferences
    {
        public int key=5, attack_key=65, stop_key=83, latency_ms=80, damage_reserve=0, input_version;
        public string activation="hold";
        public bool pause_manual=false, helper_only=false, mouse_priority=true, return_cursor=true, rmb_deny_confirmed=false;
        public bool deny=true, tower=true, quick_attack=false, show_marker=true, single_hit=true;
    }
    partial class MenuWindow
    {
        LastHitPreferences lhPrefs=new LastHitPreferences{input_version=7};
        DispatcherTimer lhTimer;
        bool lhEnabled;
        int lhRevision,lhCapture;
        string lhSession="",lhError="";
        static bool ValidLHKey(int n){return n==4||n==5||n==6||(n>=48&&n<=90)||(n>=112&&n<=123);}
        static string LHKeyName(int n){return n==4?"Колесо":n==5?"Mouse4":n==6?"Mouse5":n>=112&&n<=123?"F"+(n-111):((char)n).ToString();}
        void InitializeLastHit()
        {
            try
            {
                string file=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"last_hit_preferences.json");
                if(File.Exists(file))
                {
                    var p=new JavaScriptSerializer().Deserialize<LastHitPreferences>(File.ReadAllText(file));
                    if(p!=null&&p.input_version<4){p.pause_manual=false;p.damage_reserve=0;p.activation="hold";p.input_version=4;}
                    if(p!=null&&p.input_version<6){p.quick_attack=false;p.return_cursor=true;p.rmb_deny_confirmed=false;p.input_version=6;}
                    if(p!=null&&p.input_version<7){p.mouse_priority=true;p.input_version=7;}
                    if(p!=null&&(p.activation=="hold"||p.activation=="toggle")&&p.stop_key>=48&&p.stop_key<=90&&p.stop_key!=p.key&&p.stop_key!=p.attack_key&&p.damage_reserve>=0&&p.damage_reserve<=25&&ValidLHKey(p.key)&&p.attack_key>=48&&p.attack_key<=90&&p.key!=p.attack_key&&p.latency_ms>=0&&p.latency_ms<=300)lhPrefs=p;
                }
            }catch{}
            PreviewMouseDown+=delegate(object sender,MouseButtonEventArgs e)
            {
                if(lhCapture==0)return;
                int n=e.ChangedButton==MouseButton.XButton1?5:e.ChangedButton==MouseButton.XButton2?6:e.ChangedButton==MouseButton.Middle?4:0;
                if(lhCapture==1&&n!=0){lhPrefs.key=n;lhCapture=0;LastHitChanged();}
                else lhError="Для мыши: Mouse4, Mouse5 или колесо. Esc — отмена.";
                e.Handled=true;
            };
            lhTimer=new DispatcherTimer{Interval=TimeSpan.FromMilliseconds(400)};
            lhTimer.Tick+=delegate{WriteLastHit();};lhTimer.Start();
        }
        bool CaptureLastHitKey(KeyEventArgs e)
        {
            if(lhCapture==0)return false;
            Key k=e.Key==Key.System?e.SystemKey:e.Key;
            if(k==Key.Escape){lhCapture=0;lhError="";e.Handled=true;return true;}
            int n=KeyInterop.VirtualKeyFromKey(k);
            if((lhCapture==1&&ValidLHKey(n)&&n!=lhPrefs.attack_key&&n!=lhPrefs.stop_key)||(lhCapture==2&&n>=48&&n<=90&&n!=lhPrefs.key&&n!=lhPrefs.stop_key)||(lhCapture==3&&n>=48&&n<=90&&n!=lhPrefs.key&&n!=lhPrefs.attack_key))
            {
                if(lhCapture==1)lhPrefs.key=n;else if(lhCapture==2)lhPrefs.attack_key=n;else lhPrefs.stop_key=n;
                lhCapture=0;LastHitChanged();
            }
            else lhError="Эта клавиша зарезервирована или совпадает с другой. Esc — отмена.";
            e.Handled=true;return true;
        }
        void SyncLastHit()
        {
            if(lhSession!=session){lhSession=session;lhEnabled=false;lhRevision=0;lhCapture=0;lhError="";}
            var d=data.last_hit;
            if(d!=null&&d.revision>=lhRevision&&!d.enabled&&lhEnabled)lhEnabled=false;
        }
        void LastHitChanged()
        {
            lhRevision++;lhError="";
            try{File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"last_hit_preferences.json"),new JavaScriptSerializer().Serialize(lhPrefs),new UTF8Encoding(false));}catch{}
            WriteLastHit();
        }
        void WriteLastHit()
        {
            if(String.IsNullOrEmpty(session)||!connected)return;
            string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"last_hit_controls.json");
            string tmp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
            try
            {
                var doc=new {session=session,revision=lhRevision,utc_ms=(long)(DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalMilliseconds,
                    enabled=lhEnabled,mouse_priority=lhPrefs.mouse_priority,rmb_only=true,return_cursor=lhPrefs.return_cursor,rmb_deny_confirmed=lhPrefs.rmb_deny_confirmed,pause_manual=lhPrefs.pause_manual,helper_only=lhPrefs.helper_only,activation=lhPrefs.activation,click_mode=true,single_hit=lhPrefs.single_hit,stop_key=lhPrefs.stop_key,damage_reserve=lhPrefs.damage_reserve,quick_attack=false,show_marker=lhPrefs.show_marker,key=lhPrefs.key,attack_key=lhPrefs.attack_key,deny=lhPrefs.deny,tower=lhPrefs.tower,menu_open=IsVisible,latency_ms=lhPrefs.latency_ms};
                File.WriteAllText(tmp,new JavaScriptSerializer().Serialize(doc),new UTF8Encoding(false));
                for(int i=0;;i++)
                {
                    try{if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);break;}
                    catch(IOException){if(i>=2)throw;Thread.Sleep(2);}
                }
            }
            catch(Exception ex){lhError="Last Hit: "+ex.Message;}
            finally{try{if(File.Exists(tmp))File.Delete(tmp);}catch{}}
        }
        void FinishLastHit(){lhEnabled=false;lhRevision++;WriteLastHit();if(lhTimer!=null)lhTimer.Stop();}
        void LastHitPage()
        {
            HeroCard();
            var panel=new StackPanel();
            panel.Children.Add(MenuTheme.T("LAST HIT",15,MenuTheme.Purple,true));
            var enabled=new CheckBox{Content="Включить Last Hit",IsChecked=lhEnabled,Margin=new Thickness(0,14,0,10)};
            panel.Children.Add(enabled);
            enabled.Checked+=delegate{if(syncing)return;lhEnabled=true;LastHitChanged();};
            enabled.Unchecked+=delegate{if(syncing)return;lhEnabled=false;LastHitChanged();};
            var bind=new Button{Content="",Margin=new Thickness(0,2,0,12),HorizontalAlignment=HorizontalAlignment.Left,MinWidth=215};
            MenuTheme.AnimateButton(bind);bind.Click+=delegate{lhCapture=1;lhError="";};panel.Children.Add(bind);
            // alpha6 active path is exclusively RMB; legacy attack-key code is not exposed.
            var marker=new CheckBox{Content="Подсветка всех прочитанных линейных крипов",IsChecked=lhPrefs.show_marker,Margin=new Thickness(0,3,0,8)};
            var single=new CheckBox{Content="Одна атака · Stop после своего удара",IsChecked=lhPrefs.single_hit,Margin=new Thickness(0,3,0,8)};
            single.Checked+=delegate{if(!syncing){lhPrefs.single_hit=true;LastHitChanged();}};
            single.Unchecked+=delegate{if(!syncing){lhPrefs.single_hit=false;LastHitChanged();}};
            var activation=new ComboBox{Margin=new Thickness(0,3,0,8),MinWidth=215,HorizontalAlignment=HorizontalAlignment.Left};
            activation.Items.Add("Удержание кнопки");activation.Items.Add("Нажатие · вкл / выкл");activation.SelectedIndex=lhPrefs.activation=="toggle"?1:0;
            activation.SelectionChanged+=delegate{if(!syncing){lhPrefs.activation=activation.SelectedIndex==1?"toggle":"hold";LastHitChanged();}};
            panel.Children.Add(activation);
            var manual=new CheckBox{Content="Пауза при ручных командах",IsChecked=lhPrefs.pause_manual,Margin=new Thickness(0,3,0,8)};
            manual.Checked+=delegate{if(!syncing){lhPrefs.pause_manual=true;LastHitChanged();}};
            manual.Unchecked+=delegate{if(!syncing){lhPrefs.pause_manual=false;LastHitChanged();}};
            var helper=new CheckBox{Content="Только помощник · без нажатий",IsChecked=lhPrefs.helper_only,Margin=new Thickness(0,3,0,8)};
            helper.Checked+=delegate{if(!syncing){lhPrefs.helper_only=true;LastHitChanged();}};
            helper.Unchecked+=delegate{if(!syncing){lhPrefs.helper_only=false;LastHitChanged();}};
            panel.Children.Add(manual);panel.Children.Add(helper);panel.Children.Add(marker);
            marker.Checked+=delegate{if(!syncing){lhPrefs.show_marker=true;LastHitChanged();}};
            marker.Unchecked+=delegate{if(!syncing){lhPrefs.show_marker=false;LastHitChanged();}};
            var deny=new CheckBox{Content="Добивать союзных крипов (ПКМ)",IsChecked=lhPrefs.deny,Margin=new Thickness(0,3,0,8)};
            var tower=new CheckBox{Content="Учитывать башню и подготовительный удар",IsChecked=lhPrefs.tower,Margin=new Thickness(0,3,0,12)};
            panel.Children.Add(deny);
            var confirm=new CheckBox{Content="В Dota включена атака союзников правой кнопкой",IsChecked=lhPrefs.rmb_deny_confirmed,Margin=new Thickness(0,3,0,8)};
            confirm.Checked+=delegate{if(!syncing){lhPrefs.rmb_deny_confirmed=true;LastHitChanged();}};
            confirm.Unchecked+=delegate{if(!syncing){lhPrefs.rmb_deny_confirmed=false;LastHitChanged();}};
            panel.Children.Add(confirm);panel.Children.Add(tower);
            var cursor=new CheckBox{Content="Возвращать курсор после клика",IsChecked=lhPrefs.return_cursor,Margin=new Thickness(0,3,0,8)};
            cursor.Checked+=delegate{if(!syncing){lhPrefs.return_cursor=true;LastHitChanged();}};
            cursor.Unchecked+=delegate{if(!syncing){lhPrefs.return_cursor=false;LastHitChanged();}};
            panel.Children.Add(cursor);
            var priority=new CheckBox{Content="Короткий приоритет Last Hit при удержании ЛКМ / ПКМ",IsChecked=lhPrefs.mouse_priority,Margin=new Thickness(0,3,0,8)};
            priority.Checked+=delegate{if(!syncing){lhPrefs.mouse_priority=true;LastHitChanged();}};
            priority.Unchecked+=delegate{if(!syncing){lhPrefs.mouse_priority=false;LastHitChanged();}};
            panel.Children.Add(priority);
            deny.Checked+=delegate{if(!syncing){lhPrefs.deny=true;LastHitChanged();}};deny.Unchecked+=delegate{if(!syncing){lhPrefs.deny=false;LastHitChanged();}};
            tower.Checked+=delegate{if(!syncing){lhPrefs.tower=true;LastHitChanged();}};tower.Unchecked+=delegate{if(!syncing){lhPrefs.tower=false;LastHitChanged();}};
            var status=MenuTheme.T("",11,MenuTheme.Green,true);status.TextWrapping=TextWrapping.Wrap;panel.Children.Add(status);
            var detail=MenuTheme.T("",10,MenuTheme.Muted);detail.TextWrapping=TextWrapping.Wrap;detail.Margin=new Thickness(0,8,0,0);panel.Children.Add(detail);
            body.Children.Add(MenuTheme.Card(panel,new Thickness(18)));
            var extra=new StackPanel();extra.Children.Add(single);
            var stop=new Button{Content="",HorizontalAlignment=HorizontalAlignment.Left,Margin=new Thickness(0,8,0,6)};
            stop.Click+=delegate{lhCapture=3;lhError="";};MenuTheme.AnimateButton(stop);extra.Children.Add(stop);
            var reserveText=MenuTheme.T("",10,MenuTheme.Muted);extra.Children.Add(reserveText);
            var reserve=new Slider{Minimum=0,Maximum=25,Value=lhPrefs.damage_reserve,TickFrequency=1,IsSnapToTickEnabled=true,Margin=new Thickness(0,6,0,8)};
            reserve.ValueChanged+=delegate{if(!syncing){lhPrefs.damage_reserve=(int)reserve.Value;LastHitChanged();}};extra.Children.Add(reserve);
            var attack=new Button{Content="",HorizontalAlignment=HorizontalAlignment.Left,Margin=new Thickness(0,8,0,10)};
            attack.Click+=delegate{lhCapture=2;lhError="";};MenuTheme.AnimateButton(attack); // retained DTO/bind compatibility, not displayed in RMB mode
            var latency=MenuTheme.T("",10,MenuTheme.Muted);extra.Children.Add(latency);
            var slider=new Slider{Minimum=0,Maximum=300,Value=lhPrefs.latency_ms,TickFrequency=10,IsSnapToTickEnabled=true,Margin=new Thickness(0,8,0,8)};
            slider.ValueChanged+=delegate{if(!syncing){lhPrefs.latency_ms=(int)slider.Value;LastHitChanged();}};extra.Children.Add(slider);
            extra.Children.Add(MenuTheme.T("Только ПКМ. Режим автоатаки Dota выбираешь сам; Quick Attack не используется. Stop — только для своей команды.",10,MenuTheme.Amber));
            body.Children.Add(new Expander{Header="Управление атакой",Content=extra,Margin=new Thickness(0,15,0,10)});
            Note("Приоритет мыши: кратко отпускает удерживаемые кнопки в игре и восстанавливает только всё ещё физически удерживаемые. Плановый предел — 0,9 с; клавиши, новый клик и смена окна его прерывают. Без нового движения курсор возвращается независимо от галочки обычного возврата.");
            Note("Враг и денай — ПКМ, без A и ЛКМ. Для деная включи в Dota атаку союзников правой кнопкой и подтверди выше. Возврат курсора не перебивает новое движение мышью.");
            Note("Закрой меню и удерживай выбранную кнопку. После отпускания новые команды не отправляются; уже отданная атака и летящий снаряд не отменяются. Last Hit не преследует крипов за пределами дальности.");
            Note("Мечник, дальний, знаменосец, катапульта показываются без удержания. Серый — вне дальности, жёлтый — ожидание, красный — неизвестный урон. Числа: HP / нижний расчёт урона.");
            Note("Метка: зелёная — добивание, голубая — денай, жёлтая — подготовка/ожидание, фиолетовая — команда отправлена. Цифры HP — прогноз, не подтверждение добивания.");
            Note("Quell читается из активного предмета и его модификатора: бонус до брони, без сложения копий, не для деная. Потери HP — наблюдаемые падения, не доказательство источника удара. ? означает неизвестное, а не нулевой бонус.");
            Note("Тайминги берутся из файлов установленной игры. Прогноз — оценка: это не точный projectile manager. Автоатака самой Dota может продолжиться без команд модуля. Stop не гарантирует её постоянное отключение. При неизвестных данных отправка прекращается.");
            refresh.Add(delegate
            {
                var d=data.last_hit??new LastHitData();enabled.IsChecked=lhEnabled;enabled.IsEnabled=connected;
                bind.Content=lhCapture==1?"Нажми кнопку… Esc — отмена":(lhPrefs.activation=="toggle"?"Переключение: ":"Удержание: ")+LHKeyName(lhPrefs.key)+"  ·  изменить";
                attack.Content=lhCapture==2?"Нажми клавишу атаки…":"Атака: "+LHKeyName(lhPrefs.attack_key)+"  ·  изменить";
                stop.Content=lhCapture==3?"Нажми клавишу Stop…":"Stop: "+LHKeyName(lhPrefs.stop_key)+"  ·  изменить";
                reserveText.Text="Запас по расчётному урону: "+lhPrefs.damage_reserve+"%";
                latency.Text="Запас на ввод/сеть: "+lhPrefs.latency_ms+" мс";
                status.Text=lhError.Length>0?lhError:d.status;
                detail.Text=d.pipeline+"\n"+d.damage_status+"\n"+d.incoming_status+"\nAS: "+d.speed_source+"\nКрипов: "+d.creeps+" · Событий башни: "+d.shots+" · Отправок: "+d.sent+" · Stop: "+d.stops+"\n"+d.target+"\n"+d.reason+(d.reader_error.Length>0?"\n"+d.reader_error:"");
            });
        }
    }
    partial class OverlayContent
    {
        void DrawLastHitTarget(DrawingContext dc,float ppd)
        {
            if(menuOpen||dotaRect.IsEmpty||dotaRect.Width<260)return;
            var data=cur.dashboard;
            if(data==null||data.version!=1||data.receivedAt<=0)return;
            var d=data.last_hit;
            double elapsed=KTweaksl.Now-data.receivedAt;
            if(d==null||!d.enabled||!d.show_marker||!KTweaksl.Finite(elapsed)||elapsed<0||elapsed>.20)return;
            double statusX=dotaRect.Left+18,statusY=dotaRect.Top+(compactEnabled?220:84);
            double statusWidth=Math.Min(380,dotaRect.Width-36);
            dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(220,19,19,29)),null,
                new Rect(statusX,statusY,statusWidth,43),5,5);
            DrawCompactLine(dc,"LAST HIT · "+d.status,statusX+9,statusY+6,statusWidth-18,
                Color.FromRgb(193,176,227),reg,10,ppd);
            DrawCompactLine(dc,d.pipeline,statusX+9,statusY+24,statusWidth-18,Color.FromRgb(162,163,177),reg,9,ppd);
            foreach(var item in d.markers??new List<LastHitMarker>())
            {
                if(item==null||!item.IsFresh(elapsed)||pixelToDipX<=0||pixelToDipY<=0)continue;
                if(Math.Abs(dotaRect.Width/pixelToDipX-item.width)>2||Math.Abs(dotaRect.Height/pixelToDipY-item.height)>2)continue;
                double px=item.x*pixelToDipX+offX,py=item.y*pixelToDipY+offY;
                Color c=item.state=="last_hit"?Color.FromRgb(102,241,155):item.state=="deny"?Color.FromRgb(106,202,255):
                    item.state=="range"||item.state=="screen"?Color.FromRgb(150,154,162):item.state=="damage_unknown"?Color.FromRgb(242,132,113):Color.FromRgb(240,201,120);
                dc.DrawEllipse(null,new Pen(new SolidColorBrush(c),1.5),new Point(px,py),14,6);
                string tag=(item.label??"Крип").Split('·')[0].Trim()+" "+item.hp.ToString("0",CultureInfo.InvariantCulture)+
                    (item.state=="damage_unknown"?" / ?":" / "+item.damage.ToString("0",CultureInfo.InvariantCulture));
                DrawCompactLine(dc,tag,px-36,py-23,145,c,reg,9,ppd);
            }
            var m=d.marker;
            if(m==null||!m.IsFresh(elapsed)||!KTweaksl.Finite(pixelToDipX)||!KTweaksl.Finite(pixelToDipY)||pixelToDipX<=0||pixelToDipY<=0)return;
            if(Math.Abs(dotaRect.Width/pixelToDipX-m.width)>2||Math.Abs(dotaRect.Height/pixelToDipY-m.height)>2)return;
            double x=m.x*pixelToDipX+offX,y=m.y*pixelToDipY+offY;
            if(x<dotaRect.Left||x>dotaRect.Right||y<dotaRect.Top||y>dotaRect.Bottom)return;
            Color color=m.state=="last_hit"?Color.FromRgb(102,241,155):m.state=="deny"?Color.FromRgb(106,202,255):
                m.state=="sent"?Color.FromRgb(192,150,255):Color.FromRgb(250,201,97);
            string title=m.state=="last_hit"?"ДОБИВАНИЕ":m.state=="deny"?"ДЕНАЙ":m.state=="prepare"?"ПОДГОТОВКА":
                m.state=="sent"?"КОМАНДА ОТПРАВЛЕНА":(m.label??"").StartsWith("Ожидание атаки")?"ОЖИДАНИЕ АТАКИ":"ОЖИДАНИЕ HP";
            var pen=new Pen(new SolidColorBrush(color),2);
            dc.DrawEllipse(new SolidColorBrush(Color.FromArgb(28,color.R,color.G,color.B)),pen,new Point(x,y),23,10);
            dc.DrawEllipse(new SolidColorBrush(color),null,new Point(x,y),3,3);
            double tx=Clamp(x+29,dotaRect.Left+8,dotaRect.Right-218),ty=Clamp(y-97,dotaRect.Top+8,dotaRect.Bottom-92);
            dc.DrawLine(pen,new Point(x,y-12),new Point(tx+8,ty+81));
            dc.DrawRoundedRectangle(new SolidColorBrush(Color.FromArgb(237,17,18,25)),new Pen(new SolidColorBrush(color),1),new Rect(tx,ty,210,82),5,5);
            DrawCompactLine(dc,title,tx+8,ty+5,194,color,bold,10,ppd);
            string detail=m.state=="sent"?"HP "+m.hp.ToString("0",CultureInfo.InvariantCulture)+" · попадание не подтверждено":
                "≈ HP "+m.hp_low.ToString("0",CultureInfo.InvariantCulture)+"–"+m.hp_high.ToString("0",CultureInfo.InvariantCulture)+
                " / урон ≈"+m.damage.ToString("0",CultureInfo.InvariantCulture);
            DrawCompactLine(dc,detail,tx+8,ty+22,194,Color.FromRgb(208,207,220),reg,10,ppd);
            string stats="Мой урон "+m.hero_min.ToString("0",CultureInfo.InvariantCulture)+"–"+m.hero_max.ToString("0",CultureInfo.InvariantCulture)+
                (m.quell>0?" +Q "+m.quell.ToString("0.#",CultureInfo.InvariantCulture):"")+" · броня "+m.armor.ToString("0.#",CultureInfo.InvariantCulture);
            DrawCompactLine(dc,stats,tx+8,ty+40,194,Color.FromRgb(174,172,190),reg,10,ppd);
            string incoming=m.hp_loss_span>0?"Потери HP −"+m.hp_loss.ToString("0",CultureInfo.InvariantCulture)+" / "+m.hp_loss_span.ToString("0.00",CultureInfo.InvariantCulture)+"с · источник ?":"Потери HP: мало наблюдений";
            DrawCompactLine(dc,incoming,tx+8,ty+57,194,Color.FromRgb(174,172,190),reg,9,ppd);
        }
    }
}
