import MarkdownMessage from './MarkdownMessage';
import MessageAttachments, { type Attachment } from './MessageAttachments';
import NativeThinking from './NativeThinking';

export default function ChatMessage({role,text,files=[],thinking='',expanded=false,children,onBrowse}:{
  role:string;text:string;files?:Attachment[];thinking?:string;expanded?:boolean;children?:React.ReactNode;onBrowse:()=>void
}) {
  return <div className={`message ${role}`}>
    <span className="message-label">{role==='assistant'?'林遥 Agent':'你'}</span>
    <div className="message-bubble">
      {role==='assistant'&&<NativeThinking text={thinking} expanded={expanded}/>}
      <div className={`message-text ${role==='assistant'?'rendered':''}`}>
        {role==='assistant'?<MarkdownMessage text={text}/>:text}
      </div>
      <MessageAttachments files={files} onBrowse={onBrowse}/>
      {children}
    </div>
  </div>;
}
