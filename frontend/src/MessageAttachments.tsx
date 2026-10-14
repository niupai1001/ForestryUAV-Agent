import { File, FolderOpen } from 'lucide-react';

export type Attachment = {resource_id:string;name:string;kind:string;preview_url?:string|null};
export default function MessageAttachments({files,onBrowse}:{files:Attachment[];onBrowse:()=>void}) {
  if(!files.length)return null;
  return <div className="message-attachments" aria-label="消息附件">{files.map(file=><div key={file.resource_id}>
    {file.preview_url?<a href={file.preview_url} target="_blank" rel="noopener noreferrer"><img src={file.preview_url} alt={file.name}/><span>{file.name}</span></a>:
      <button onClick={onBrowse}>{file.kind==='directory'?<FolderOpen size={17}/>:<File size={17}/>}<span>{file.name}</span></button>}
  </div>)}</div>;
}
