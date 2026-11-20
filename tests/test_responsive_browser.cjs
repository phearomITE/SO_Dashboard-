const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL || undefined});
try{const connections={groups:[],scopes:['R3-KKG3'],topics:{},central_chat_id:0,roster_count:231,location_count:0};let savedGroup=null;const page=await browser.newPage();await page.route('**/*',async route=>{
 const url=new URL(route.request().url());
 if(url.hostname!=='dashboard.test')return route.abort();
 if(url.pathname==='/health')return route.fulfill({json:{status:'ready',database:true}});
 if(url.pathname==='/api/v1/admin/telegram/groups'){savedGroup=route.request().postDataJSON();connections.groups=[{...savedGroup,title:'KKG3 reporting',active:true}];return route.fulfill({json:{saved:true}});}
 if(url.pathname==='/api/v1/admin/telegram')return route.fulfill({json:connections});
 if(url.pathname.startsWith('/api/'))return route.fulfill({json:{items:[],rows:[],attendance:[],summary:{},totals:{},employees:[],leaves:[],runs:[],count:0}});
 if(url.pathname==='/static/dashboard.html')return route.fulfill({body:fs.readFileSync(path.join(root,'app/static/dashboard.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace('<html lang="en">','<html lang="en" class="embedded">'),contentType:'text/html'});
 const file=url.pathname.startsWith('/static/')?path.join(root,'app',url.pathname):path.join(root,'app/static/admin',url.pathname==='/'?'index.html':url.pathname);
 if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
 return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'text/html'});
 });
 for(const width of [320,390,768,1024,1440]){
 await page.setViewportSize({width,height:850});await page.goto('http://dashboard.test/');
 await page.locator('.sidebar .nav button').first().waitFor();
 for(const name of ['Live Map Check In','AL','Plan Sale','Reason Summary','Report GP','Master Sale','Performance','Setting']){
 const nav=page.getByRole('button',{name,exact:true});await nav.scrollIntoViewIfNeeded();await nav.click();await page.waitForTimeout(280);
 assert.equal(await page.locator('.dashboard-recovery').count(),0,`${name} crashed`);
 if(name==='Live Map Check In'){const frame=page.frames().find(f=>f.url().includes('/static/dashboard.html'));assert.ok(frame);const map=await frame.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,map:document.querySelector('.mapwrap').getBoundingClientRect().toJSON(),side:document.querySelector('.side').getBoundingClientRect().toJSON()}));assert.ok(map.scroll<=map.width+1,`Embedded map overflows at ${width}`);if(width<=700)assert.ok(map.side.y>=map.map.bottom-2,`Route must stack below map at ${width}`);}
 const geometry=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,nav:document.querySelector('.sidebar').getBoundingClientRect().toJSON()}));
 assert.ok(geometry.scroll<=width+1,`${name} overflows at ${width}: ${geometry.scroll}`);
 if(width<=700){assert.equal(Math.round(geometry.nav.width),width);assert.ok(geometry.nav.y>700,'Phone menu must be at bottom');}
 }
 if(width===390){
 await page.getByLabel('Telegram Chat ID',{exact:true}).fill('-100123');
 await page.getByLabel('Region / Dealer',{exact:true}).selectOption('R3-KKG3');
 await page.getByRole('button',{name:'Connect group',exact:true}).click();
 await page.getByText('Connection saved. New reports will use this configuration.',{exact:true}).waitFor();
 assert.deepEqual(savedGroup,{chat_id:-100123,code:'R3-KKG3',topic_id:null});
 assert.equal(await page.getByText('KKG3 reporting',{exact:true}).count(),1);
 }
 console.log(`PASS: 8 page layouts and embedded-map structure at ${width}px`);
 }
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
