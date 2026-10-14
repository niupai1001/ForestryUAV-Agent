import Markdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './Conversation.css';

export default function MarkdownMessage({text}:{text:string}) {
  return <div className="markdown-message"><Markdown remarkPlugins={[remarkGfm]} skipHtml
    urlTransform={defaultUrlTransform} components={{
      a:({node,...props})=><a {...props} target="_blank" rel="noopener noreferrer"/>,
      img:({src,alt})=>src&&/^\/(resources|artifacts)\//.test(src)?<img src={src} alt={alt||'图片'} loading="lazy"/>:<span>{alt||'图片链接'}</span>
    }}>{text}</Markdown></div>;
}
