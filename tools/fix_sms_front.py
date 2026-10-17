from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\templates\accounts_add.html")
t = p.read_text(encoding="utf-8")

old = '''/* ---------- 手机验证码 ---------- */
var smsCap='', smsBuid=null;
document.getElementById('smsSendBtn').onclick=async function(){
  var b=this, tel=document.getElementById('smsTel').value.trim();
  if(tel.length<6){ BP.toast('请输入正确的手机号','err'); return; }
  var px=pxUse(); b.disabled=true;
  try{
    var d=await BP.api('/api/accounts/sms/send',{method:'POST',body:JSON.stringify({
      tel:tel, cid:document.getElementById('smsCid').value, proxy:px||''})});
    smsCap=d.captcha_key||'';
    smsBuid={buvid3:d.buvid3||'', buvid4:d.buvid4||''};
    document.getElementById('sms-tip').textContent='验证码已发送到 '+tel+'，请查看短信'+(d.is_new?'（该手机号在 B 站尚未注册）':'');
    BP.cd(b,60,'获取验证码');
  }catch(e){ BP.toast(e.message,'err'); b.disabled=false; }
};'''

new = '''/* ---------- 手机验证码 ---------- */
/* B站 发短信前要过极验：先拿 gt/challenge，跑滑块拿 validate+seccode，再发。
   没有这些参数 B站 一律返回 -105「验证码错误」。 */
var smsCap='', smsBuid=null, GT_LOADING=null;

function loadGeetest(){
  if(window.initGeetest4) return Promise.resolve();
  if(GT_LOADING) return GT_LOADING;
  GT_LOADING=new Promise(function(res, rej){
    var s=document.createElement('script');
    s.src='https://static.geetest.com/v4/gt4.js';
    s.onload=function(){ res(); };
    s.onerror=function(){ rej(new Error('极验脚本加载失败（可能被网络或插件拦截）')); };
    document.head.appendChild(s);
    setTimeout(function(){ if(!window.initGeetest4) rej(new Error('极验脚本加载超时')); }, 12000);
  });
  return GT_LOADING;
}

async function runGeetest(cap){
  await loadGeetest();
  return new Promise(function(res, rej){
    initGeetest4({captchaId: cap.gt, product:'bind'}, function(gt){
      gt.onSuccess(function(){
        var r=gt.getValidate()||{};
        res({validate: r.lot_number || '', seccode: (r.captcha_output||'')+'|'+(r.pass_token||'')+'|'+(r.gen_time||'')});
      });
      gt.onError(function(){ rej(new Error('滑块验证失败，请重试')); });
      gt.onClose(function(){ rej(new Error('已取消滑块验证')); });
      gt.showBox();
    });
  });
}

document.getElementById('smsSendBtn').onclick=async function(){
  var b=this, tel=document.getElementById('smsTel').value.trim();
  if(tel.length<6){ BP.toast('请输入正确的手机号','err'); return; }
  var px=pxUse(); b.disabled=true;
  var tip=document.getElementById('sms-tip');
  try{
    tip.textContent='正在获取验证参数…';
    var cap=await BP.api('/api/accounts/sms/captcha');
    var parts={};
    if(cap.type==='geetest' && cap.gt){
      tip.textContent='请完成滑块验证…';
      parts=await runGeetest(cap);
      tip.textContent='正在发送短信…';
    }
    var d=await BP.api('/api/accounts/sms/send',{method:'POST',body:JSON.stringify({
      tel:tel, cid:document.getElementById('smsCid').value, proxy:px||'',
      token:cap.token||'', challenge:cap.challenge||'',
      geetest_validate:parts.validate||'', seccode:parts.seccode||''})});
    smsCap=d.captcha_key||'';
    smsBuid={buvid3:d.buvid3||'', buvid4:d.buvid4||''};
    tip.textContent='验证码已发送到 '+tel+'，请查看短信'+(d.is_new?'（该手机号在 B 站尚未注册）':'');
    BP.cd(b,60,'获取验证码');
  }catch(e){
    tip.textContent='';
    BP.toast(e.message,'err');
    b.disabled=false;
  }
};'''

assert old in t, "sms block not found"
t = t.replace(old, new, 1)

# 二维码渲染加个兜底：拿不到图片时给可点链接
old_qr = '''  document.getElementById('qr-wrap').innerHTML='<img src="'+d.qr+'" style="max-width:200px">';'''
new_qr = '''  if(d.qr){
    document.getElementById('qr-wrap').innerHTML=
      '<img src="'+d.qr+'" style="max-width:200px;display:block" alt="登录二维码">';
  }else{
    document.getElementById('qr-wrap').innerHTML=
      '<p class="hint">二维码生成失败，请刷新重试</p>';
  }'''
if old_qr in t:
    t = t.replace(old_qr, new_qr, 1)

p.write_text(t, encoding="utf-8")
print("accounts_add.html: 接上极验 + 二维码渲染兜底")
