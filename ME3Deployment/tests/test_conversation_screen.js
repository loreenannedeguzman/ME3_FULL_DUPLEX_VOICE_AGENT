'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync(process.argv[2],'utf8').replace('refresh();setInterval(refresh,750);','');
function context(){
  const elements=new Map();
  const element=()=>({content:'fixture',value:'',style:{},classList:{add(){},remove(){},toggle(){}},setAttribute(){},replaceChildren(){},append(){},scrollHeight:0,scrollTop:0,clientHeight:0});
  const ctx=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},querySelector:element,createElement:element},setTimeout(fn){fn();},requestAnimationFrame(){}});
  vm.runInContext(src,ctx);
  vm.runInContext("var polls=0;var calls=[];request=async action=>{calls.push(action);return {ok:true}};var gates={normal:{ready:true},text:{ready:true}};",ctx);
  return ctx;
}
(async()=>{
  const results=[];
  for(const name of ['text','voice']){
    const ctx=context();
    vm.runInContext(name==='text'?"current={active:false,mode:'standby',state:'stopped',gates};refresh=async()=>{polls++;current=polls===1?{active:false,mode:'standby',state:'stopped',gates}:{active:true,mode:'text',state:'ready',can_send:true,gates}};":"current={active:true,mode:'text',state:'ready',can_send:true,gates};refresh=async()=>{polls++;current=polls===1?{active:true,mode:'text',state:'ready',can_send:true,gates}:{active:true,mode:'voice',state:'listening',can_send:false,gates}};",ctx);
    try{await vm.runInContext('switchMode('+JSON.stringify(name)+')',ctx);assert.ok(vm.runInContext('polls>=2',ctx),'stale state must not settle mode switch');assert.equal(vm.runInContext('current.mode',ctx),name);results.push({id:'stale_poll_during_'+name+'_start',status:'PASS'});}
    catch(error){results.push({id:'stale_poll_during_'+name+'_start',status:'FAIL',reason:error.message});}
  }
  {const ctx=context();try{vm.runInContext("disconnected=true;$('notice').textContent='Connection lost';$('notice').hidden=false;draw({mode:'standby',state:'stopped',active:false,gates,messages:[],tasks:[],reminders:{reminders:[]}});",ctx);assert.equal(vm.runInContext("$('notice').hidden",ctx),true);results.push({id:'reconnection_clears_stale_warning',status:'PASS'});}catch(error){results.push({id:'reconnection_clears_stale_warning',status:'FAIL',reason:error.message});}}
  console.log(JSON.stringify({checks:results,network_calls:0,provider_calls:0}));if(results.some(r=>r.status!=='PASS'))process.exitCode=1;
})();
