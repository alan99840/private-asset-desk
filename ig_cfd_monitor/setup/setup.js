const form=document.querySelector('#setup-form'),result=document.querySelector('#save-result'),select=document.querySelector('#profile-select');
const labels={waiting_for_credentials:'等待填写 API 信息',waiting_for_service:'等待后台连接',connecting:'正在验证 IG 登录',connected:'已连接',partial:'部分数据重试中',paused:'已暂停',error:'连接未成功',account_selection_required:'请选择启用的 HKD CFD Account ID'};
let profiles=[],selected='default',initialized=false,optionKey='';
function edit(id){selected=id;select.value=id;form.reset();result.textContent='';const saved=profiles.find(p=>p.credentials.id===id)?.credentials;const exists=!!saved?.configured;
 form.elements.name.value=saved?.name||(id==='new'?'':'主账户');form.elements.environment.value=saved?.environment||'live';form.elements.environment.disabled=exists;form.elements.account_id.value=saved?.account_id||'';form.elements.history_since.value=saved?.history_since||'';form.elements.enabled.checked=saved?.enabled!==false;
 for(const field of ['api_key','identifier','password']){form.elements[field].value='';form.elements[field].required=!exists;}
 if(exists)result.textContent='已有凭据已保存。密钥、用户名和密码留空即可保留。';
}
async function status(){try{const r=await fetch('/api/profiles',{cache:'no-store'});if(!r.ok)throw Error();profiles=(await r.json()).profiles;const key=JSON.stringify(profiles.map(p=>[p.credentials.id,p.credentials.name]));
 if(key!==optionKey){select.replaceChildren();for(const p of profiles){const o=document.createElement('option');o.value=p.credentials.id;o.textContent=p.credentials.name;select.append(o);}const o=document.createElement('option');o.value='new';o.textContent='＋ 新增 IG 登录账户';select.append(o);select.value=selected;optionKey=key;}
 if(!initialized){initialized=true;edit(selected);}
 const list=document.querySelector('#account-choices');list.replaceChildren();document.querySelector('#connection-status').textContent=`已配置 ${profiles.filter(p=>p.credentials.configured).length} 个账户 · 每 3 秒检查`;
 for(const {credentials:c,connection:s} of profiles){const item=document.createElement('article');item.className='account-option';const h=document.createElement('strong');h.textContent=`${c.name} · ${(c.environment||'待配置').toUpperCase()}`;item.append(h);const p=document.createElement('p');p.textContent=(labels[s.status]||s.status||'待连接')+(s.message?' · '+s.message:'');item.append(p);if(s.at){const t=document.createElement('small');t.textContent='后台状态时间：'+new Date(s.at).toLocaleString('zh-HK',{timeZone:'Asia/Hong_Kong',hour12:false});item.append(t);}
 for(const a of s.accounts||[]){const row=document.createElement('small');row.textContent=`${a.accountId} · ${a.accountType} · ${a.currency} · ${a.status}`;item.append(row);}list.append(item);}
 }catch{document.querySelector('#connection-status').textContent='本机配置服务暂不可用。';}}
select.addEventListener('change',()=>edit(select.value));document.querySelector('#add-profile').addEventListener('click',()=>edit('new'));
form.addEventListener('submit',async e=>{e.preventDefault();const b=form.querySelector('button');b.disabled=true;result.className='';result.textContent='正在保存…';
 try{const payload={...Object.fromEntries(new FormData(form)),profile_id:selected,environment:form.elements.environment.value,enabled:form.elements.enabled.checked};const r=await fetch('/api/configure',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify(payload)});const data=await r.json();if(!r.ok)throw Error(data.error||'保存失败');selected=data.profile_id;await status();edit(selected);result.textContent=data.message;result.className='success';
 }catch(e){result.textContent=e.message;result.className='error';}finally{b.disabled=false;}});status();setInterval(status,3000);
