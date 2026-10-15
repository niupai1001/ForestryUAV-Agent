export default function NativeThinking({text,streaming=false}:{text:string;streaming?:boolean}) {
  if(!text)return null;
  return <details className="native-thinking" aria-label="模型思考" open={streaming}>
    <summary>{streaming?'思考中':'思考'}</summary>
    <div className="thinking-text">{text}</div>
  </details>;
}
