import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import JobsPanel from '../../src/JobsPanel';
import '../../src/style.css';

function Fixture() {
  const [error,setError]=useState('');
  return <><JobsPanel taskId="task-fixture" refreshKey="fixture" onError={setError}
    messages={[{role:'user',turn_id:'source-turn',text:'拼接本次多光谱航片并保留结果'}]}
    artifacts={[{artifact_id:'report-fixture',name:'航次报告.pdf'}]}/>{error&&<p role="alert">{error}</p>}</>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
