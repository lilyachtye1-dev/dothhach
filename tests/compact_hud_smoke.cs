    internal static class CompactHudSmoke
    {
        static int count;
        static void Check(bool value,string name) { if(!value)throw new Exception(name);count++; }
        static CompactHudView View(DashboardData data) { return CompactHudView.Build(data,10.1); }
        public static void Main(string[] args)
        {
            DashboardData data=new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<DashboardData>(System.IO.File.ReadAllText(args[0]));
            data.receivedAt=10;data.stale_limit=.25;
            CompactHudView view=View(data);
            Check(view.live,"actual Python dashboard is fresh");
            Check(view.hero=="Anti-Mage","actual Python hero label");
            Check(!view.ksOn&&view.dodgeOn,"independent ON/OFF");
            Check(view.ks=="KS · OFF"&&view.dodge=="DODGER · ON","independent labels");
            Check(view.reason=="Assassinate: нет валидной цели","actual compact reason deserialized");
            Check(view.mode=="AUTO","mode");
            Check(view.last.Contains("Нажатий ещё не было"),"no invented input");
            data.armed=true;data.dodger.armed=false;view=View(data);
            Check(view.ksOn&&!view.dodgeOn,"inverse independent arming");
            data.dodger.armed=true;data.mode=data.dodger.mode="PREVIEW";view=View(data);
            Check(!view.ksOn&&!view.dodgeOn,"preview never green input ON");
            Check(view.ks.Contains("PREVIEW")&&view.dodge.Contains("PREVIEW"),"preview labels");
            data.mode="AUTO";view=View(data);
            Check(view.ksOn&&!view.dodgeOn,"module mode independence");
            data.dodger.mode="AUTO";data.dodger.available=false;view=View(data);
            Check(!view.dodgeOn&&view.dodge.Contains("БЛОК"),"unavailable detector");
            data.dodger.available=true;data.dodger.pending=true;view=View(data);
            Check(!view.dodgeOn&&view.dodge.Contains("ОЖИДАНИЕ"),"pending request is not ON");
            data.dodger.pending=false;
            data.dodger.last_action="SENT · Counterspell";data.dodger.event_age=1.95;view=View(data);
            Check(view.last=="Последнее: SENT · Counterspell · 2 с назад","last action age");
            data.dodger.event_age=Double.NaN;Check(!View(data).last.Contains("назад"),"nonfinite event age");
            data.age=.3;view=View(data);
            Check(!view.live&&!view.ksOn&&!view.dodgeOn,"stale never green");
            Check(view.mode=="НЕТ ДАННЫХ"&&view.ks=="KS · —"&&view.dodge=="DODGER · —","stale labels");
            Check(view.reason.Contains("Нет свежих данных"),"stale reason");
            data.age=Double.NaN;Check(!View(data).live,"nonfinite reader age");
            data.age=-1;Check(!View(data).live,"negative age");
            data.age=0;data.receivedAt=11;Check(!View(data).live,"future receive time");
            data.receivedAt=0;Check(!View(data).live,"never received");
            data.receivedAt=10;data.stale_limit=Double.PositiveInfinity;Check(!View(data).live,"invalid stale limit");
            data.stale_limit=.25;data.fresh=false;Check(!View(data).live,"fresh flag false");
            data.fresh=true;data.version=0;Check(!View(data).live,"old protocol without dashboard");
            Check(!CompactHudView.Build(null,10.1).live,"null dashboard");
            data.version=1;data.dodger=null;Check(!View(data).dodgeOn,"missing dodger DTO");
            Console.WriteLine("PASS: "+count+" actual native HUD/DTO assertions (not WPF rendering)");
        }
    }
