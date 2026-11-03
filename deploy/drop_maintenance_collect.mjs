// Background-only original evidence collection; called by drop_maintenance.py.
const fs=await import('node:fs/promises');
const root='/Users/apple/orca/projects/familypartners/';
const request=JSON.parse(await fs.readFile(root+'_workspace/drop-maintenance-request.json','utf8'));
const t=await taskSpace('fp-drop-maintenance-'+request.run_id),p=t.page('p1');
let successful=false;
try{
 await p.goto('https://www.reddit.com/r/n8n/new/');
 await p.waitForFunction(()=>document.querySelectorAll('shreddit-post').length>0);
 let candidates=await p.evaluate(()=>[...document.querySelectorAll('shreddit-post')].map(e=>({title:e.getAttribute('post-title'),url:e.getAttribute('permalink'),date:e.getAttribute('created-timestamp'),text:e.innerText})).filter(x=>x.url&&x.date));
 const used=new Set(request.used_urls.map(s=>s.replace('https://www.reddit.com','https://reddit.com').replace(/\/$/,'')));
 const eligible=x=>{const age=Date.now()-new Date(x.date).getTime();return age>=0&&age<7*86400000&&!used.has(('https://reddit.com'+x.url).replace(/\/$/,''))&&/workflow|built|automat|template/i.test(x.title)&&x.text.length>600;};
 if(candidates.filter(eligible).length<2){
  await p.goto('https://www.reddit.com/r/n8n/top/?t=week');
  await p.waitForFunction(()=>document.querySelectorAll('shreddit-post').length>0);
  const other=await p.evaluate(()=>[...document.querySelectorAll('shreddit-post')].map(e=>({title:e.getAttribute('post-title'),url:e.getAttribute('permalink'),date:e.getAttribute('created-timestamp'),text:e.innerText})).filter(x=>x.url&&x.date));
  candidates=[...new Map([...candidates,...other].map(x=>[x.url,x])).values()];
 }
 const recent=candidates.filter(eligible);
 await fs.writeFile(request.output+'/reddit-discovery.json',JSON.stringify({candidates,recent},null,2));
 const verified=[];
 for(const candidate of recent.slice(0,5)){
  await p.goto('https://www.reddit.com'+candidate.url);
  await p.waitForFunction(()=>document.querySelector('shreddit-post')&&document.querySelector('main'));
  await p.waitForFunction(()=>document.querySelector('shreddit-comment')||document.querySelector('main')?.innerText.includes('댓글 섹션'),undefined,{timeout:15000});
  const x=await p.evaluate(()=>({url:location.href,title:document.title,text:document.querySelector('main').innerText,times:[...document.querySelectorAll('time')].map(e=>({date:e.dateTime,text:e.innerText})),author:document.querySelector('shreddit-post').getAttribute('author'),post_title:document.querySelector('shreddit-post').getAttribute('post-title'),created:document.querySelector('shreddit-post').getAttribute('created-timestamp')}));
  if(x.text.length<600||!x.times[0]?.date||/blocked by network security|page not found/i.test(x.text))continue;
  const key='reddit-'+verified.length;
  await fs.writeFile(request.output+'/'+key+'.json',JSON.stringify(x,null,2));
  await p.screenshot({path:request.output+'/'+key+'.png'});verified.push({key,...x});
  if(verified.length>=2)break;
 }
 if(!verified.length)throw Error('no_readable_recent_reddit_original');
 await fs.writeFile(request.output+'/reddit-verified.json',JSON.stringify(verified,null,2));successful=true;
 console.log(JSON.stringify({spaceId:t.spaceId,verified:verified.length}));
}finally{
 // No user-owned pages are created or adopted by this collector.
 if(successful)await t.finish({keep:[]});
 else console.error(JSON.stringify({unfinishedSpace:t.spaceId}));
}
