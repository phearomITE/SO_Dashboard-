const {JSDOM}=require('/ui/node_modules/jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'https://dashboard.test'});
Object.assign(global,{window:dom.window,document:dom.window.document,Event:dom.window.Event,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true});
const React=require('/ui/node_modules/react'),{act}=React,{createRoot}=require('/ui/node_modules/react-dom/client');
const Login=require('/ui/email-login.cjs').default;
let calls=[],account=null;
global.fetch=async(path,options)=>{calls.push([path,JSON.parse(options.body)]);return {ok:true,json:async()=>path.endsWith('/request')?{challenge_id:'a'.repeat(48),resend_after:60}:{access_token:'session-test',email:'user@yahoo.com',role:'viewer'}}};
function input(node,value){Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(node,value);node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}))}
(async()=>{const root=createRoot(document.getElementById('root'));
await act(async()=>{root.render(React.createElement(Login,{onLogin:value=>account=value}))});
await act(async()=>{input(document.querySelector('input[type=email]'),'user@yahoo.com')});
await act(async()=>{document.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,5))});
if(calls[0][1].email!=='user@yahoo.com'||!document.body.textContent.includes('Check your email'))throw Error('Request-code step failed');
if(![...document.querySelectorAll('button')].some(x=>x.textContent==='Resend in 60s'&&x.disabled))throw Error('Resend cooldown missing');
await act(async()=>{input(document.querySelector('[aria-label="Verification code"]'),'123456')});
await act(async()=>{document.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,5))});
if(calls[1][1].code!=='123456'||account?.role!=='viewer'||sessionStorage.getItem('so-dashboard-session-v1')!=='session-test')throw Error('Code verification failed');
await act(async()=>root.unmount());console.log('PASS: email request, resend cooldown, verification, and session handoff');
})().catch(e=>{console.error(e);process.exitCode=1});
