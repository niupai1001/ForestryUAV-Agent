import { useEffect, useState } from 'react';

export function ModelWait({startedAt}:{startedAt?:string}) {
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  return <span className="model-wait" role="status">等待模型输出{startedAt?` · ${Math.max(0,Math.floor((now-Date.parse(startedAt))/1000))}秒`:''}</span>;
}

export default function NativeThinking({text,streaming=false,expanded=false}:{text:string;streaming?:boolean;expanded?:boolean}) {
  if(!text)return null;
  return <details className="native-thinking" aria-label="模型思考" open={streaming||expanded}>
    <summary>{streaming?'思考中':'思考'}</summary>
    <div className="thinking-text">{text}</div>
  </details>;
}
