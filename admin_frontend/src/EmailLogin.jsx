import React,{useEffect,useState} from "react";
const SESSION_KEY="so-dashboard-session-v1";
export default function EmailLogin({ onLogin }) {
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);
  useEffect(() => { if (!wait) return; const timer=setTimeout(()=>setWait(v=>v-1),1000); return ()=>clearTimeout(timer); }, [wait]);
  async function send() {
    setError('');setBusy(true);
    try {
      const response=await fetch('/api/v1/auth/email/request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
      const body=await response.json();
      if(!response.ok) throw Error(body.detail||'Could not send verification email.');
      setChallenge(body.challenge_id);setCode('');setWait(body.resend_after);
    } catch(err){setError(err.message)}finally{setBusy(false)}
  }
  async function submit(event) {
    event.preventDefault();
    if(!challenge){await send();return}
    setError('');setBusy(true);
    try {
      const response=await fetch('/api/v1/auth/email/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,code,challenge_id:challenge})});
      const body=await response.json();
      if(!response.ok) throw Error(body.detail||'Verification failed.');
      sessionStorage.setItem(SESSION_KEY,body.access_token);
      onLogin(body);
    }catch(err){setError(err.message)}finally{setBusy(false)}
  }
  return <main className="login-screen"><form className="panel login-card email-login-card" onSubmit={submit}>
    <img src="/assets/cmfb-logo.png" alt="Chip Mong Foodbev" />
    <span className="email-login-eyebrow">SO OPERATIONS CENTER</span>
    <h1>{challenge?'Check your email':'Welcome back'}</h1>
    <p>{challenge?`Enter the six-digit code sent to ${email}. It expires in 10 minutes.`:'Sign in with your email address. We will send you a one-time verification code.'}</p>
    <label className="field"><span>Email address</span><input type="email" autoComplete="email" maxLength={254} value={email} disabled={Boolean(challenge)||busy} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com" required /></label>
    {challenge&&<label className="field"><span>Verification code</span><input className="email-code-input" aria-label="Verification code" autoFocus inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/[^0-9]/g,''))} placeholder="000000" required /></label>}
    {error&&<p className="api-error" role="alert">{error}</p>}
    <button className="primary" disabled={busy}>{busy?'Please wait…':challenge?'Verify & sign in':'Send verification code'}</button>
    {challenge&&<div className="email-login-actions"><button type="button" className="secondary" disabled={busy||wait>0} onClick={send}>{wait?`Resend in ${wait}s`:'Resend code'}</button><button type="button" className="secondary" disabled={busy} onClick={()=>{setChallenge(null);setCode('');setError('')}}>Change email</button></div>}
    
  </form></main>;
}
