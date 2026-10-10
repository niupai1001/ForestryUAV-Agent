export async function api<T>(path:string,payload?:object,method='POST'):Promise<T> {
  const reply=await fetch(path,payload?{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});
  const data=await reply.json();
  if(!reply.ok)throw new Error(data.error?.message||`请求失败 (${reply.status})`);
  return data;
}
