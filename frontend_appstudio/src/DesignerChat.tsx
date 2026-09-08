import {useEffect, useRef, useState} from 'react';
import {ArrowRight, Sparkles, RefreshCw, Send} from 'lucide-react';
export type DesignTurn = {role:'user'|'assistant';content:string};
type Props = {
  source:string; label:string; sample:boolean; name:string; brief:string;
  turns:DesignTurn[]; suggestions:string[]; mode?:string; building:boolean;
  request:(messages:DesignTurn[])=>Promise<any>;
  onUpdate:(value:{turns:DesignTurn[];suggestions:string[];prompt:string;name:string;designMode:string})=>void;
  onName:(name:string)=>void; onBuild:()=>void; onBack:()=>void;
};
export default function DesignerChat(p:Props) {
  const [message,setMessage]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const alive=useRef(true), bottom=useRef<HTMLDivElement>(null);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
  useEffect(()=>{bottom.current?.scrollIntoView({block:'nearest',behavior:'smooth'})},[p.turns,busy]);
  const locked=busy||p.building, full=p.turns.length>=24;
  async function send(text:string){
    if(locked||full||text.trim().length<3)return;
    const turns:DesignTurn[]=[...p.turns,{role:'user',content:text.trim()}];
    setBusy(true);setError('');
    try{
      const reply=await p.request(turns);
      if(!alive.current)return;
      p.onUpdate({turns:[...turns,{role:'assistant',content:reply.message}],suggestions:reply.suggestions,prompt:reply.brief,name:p.name||reply.name,designMode:reply.mode});
      setMessage('');
    }catch(e){if(alive.current)setError((e as Error).message)}
    finally{if(alive.current)setBusy(false)}
  }
  const suggestions=p.turns.length?p.suggestions:['Help me focus on work that needs attention','Give me an overview of progress','Help me find and compare records'];
  return <div className="designer-layout">
    <section className="designer-chat" aria-label="App design conversation">
      <header className="designer-header"><span className="copilot-mark"><Sparkles size={20}/></span><div><h1>Let’s build your app.</h1><p>Pilant AI · {p.label}</p></div><span className="designer-source">{p.sample?'Sample data':'Connected'}</span></header>
      <div className="designer-thread" role="log" aria-label="Design messages">
        <div className="designer-message assistant"><strong>Pilant AI</strong><p>What should this app help you do with {p.label}? Tell me about your work, and we’ll shape the interface together.</p></div>
        {p.turns.map((turn,i)=><div className={`designer-message ${turn.role}`} key={i}><strong>{turn.role==='user'?'You':'Pilant AI'}</strong><p>{turn.content}</p></div>)}
        {busy&&<div className="designer-thinking"><RefreshCw size={15} className="spin"/>Thinking about your interface…</div>}
        <div ref={bottom}/>
      </div>
      {!full&&<div className="designer-suggestions" aria-label="Suggested replies">{suggestions.map(text=><button key={text} disabled={locked} onClick={()=>send(text)}>{text}<ArrowRight size={13}/></button>)}</div>}
      {error&&<div className="error" role="alert">{error}</div>}
      <form className="designer-composer" onSubmit={e=>{e.preventDefault();send(message)}}>
        <label className="sr-only" htmlFor="design-message">Message Pilant AI</label>
        <textarea id="design-message" value={message} onChange={e=>setMessage(e.target.value)} disabled={locked||full} maxLength={2000} rows={3} placeholder="Describe your work, ask a question, or change the design…" onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send(message)}}}/>
        <div><small>{full?'Conversation complete. Build your app or start a new conversation.':'Enter to send · Shift + Enter for a new line'}</small><button className="primary" aria-label="Send design message" disabled={locked||full||message.trim().length<3}><Send size={16}/></button></div>
      </form>
      {p.mode==='guided'&&<p className="designer-fallback">AI conversation is unavailable. Guided questions are helping collect your preferences.</p>}
    </section>
    <aside className="designer-brief" aria-label="Your app brief">
      <span className="eyebrow">TAKING SHAPE</span><h2>Your app brief</h2>
      <p>Review the plan as we talk. You can build whenever you’re ready.</p>
      <label>App name<input value={p.name} maxLength={80} placeholder={`My ${p.label} app`} disabled={locked} onChange={e=>p.onName(e.target.value)}/></label>
      <div className="designer-plan">{p.brief||'Your goal, screens and working preferences will appear here after your first message.'}</div>
      <button className="primary wide" disabled={locked||p.brief.trim().length<10||!p.name.trim()} onClick={p.onBuild}>{p.building?<><RefreshCw size={16} className="spin"/>Building your interface…</>:<>Build my app<ArrowRight size={16}/></>}</button>
      <small>Your app will use {p.label} data and include Pilant Copilot.</small>
      <button className="text-button" disabled={locked} onClick={p.onBack}>← Change software</button>
      {!!p.turns.length&&<button className="text-button" disabled={locked} onClick={()=>{setMessage('');setError('');p.onUpdate({turns:[],suggestions:[],prompt:'',name:p.name,designMode:''})}}>Start a new conversation</button>}
    </aside>
  </div>
}
