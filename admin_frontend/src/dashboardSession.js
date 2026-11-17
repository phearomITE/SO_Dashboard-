const KEY='so-dashboard-session-v1';
let restoring=null;
export async function restoreSession(){
 if(!restoring)restoring=(async()=>{const response=await fetch('/api/v1/auth/restore',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});const data=await response.json().catch(()=>({}));if(!response.ok){const error=new Error(data.detail||'Please sign in again.');error.status=response.status;throw error}sessionStorage.setItem(KEY,data.access_token);return data})().finally(()=>{restoring=null});
 return restoring;
}
export async function sessionFetch(path,options={}){
 const request=()=>{const headers=new Headers(options.headers||{});const token=sessionStorage.getItem(KEY);if(token)headers.set('Authorization',`Bearer ${token}`);return fetch(path,{...options,headers,credentials:'same-origin'})};
 let response=await request();
 if(response.status===401){try{await restoreSession();response=await request()}catch(error){if(error.status===401){sessionStorage.removeItem(KEY);window.dispatchEvent(new Event('so-dashboard-session-expired'))}throw error}}
 return response;
}
