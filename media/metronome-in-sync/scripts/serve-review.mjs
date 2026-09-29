import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
const base=path.resolve(import.meta.dirname,'..');
const port=Number(process.env.FILM_REVIEW_PORT||4392);
if(!Number.isInteger(port)||port<1||port>65535)throw new Error('FILM_REVIEW_PORT must be a valid TCP port');
const types={'.html':'text/html; charset=utf-8','.png':'image/png','.jpg':'image/jpeg','.css':'text/css','.js':'text/javascript','.json':'application/json','.md':'text/plain; charset=utf-8','.ttf':'font/ttf','.svg':'image/svg+xml','.mp4':'video/mp4','.srt':'text/plain; charset=utf-8','.woff2':'font/woff2'};
const server=http.createServer((req,res)=>{
  try{
    const url=new URL(req.url,'http://127.0.0.1');const rel=decodeURIComponent(url.pathname==='/'?'/review.html':url.pathname);
    const target=path.resolve(base,'.'+rel),ext=path.extname(target);
    if(!target.startsWith(base+path.sep)||rel.split('/').some(x=>x.startsWith('.')||x==='node_modules')||!types[ext]){res.writeHead(403);res.end('Unavailable');return;}
    if(!fs.existsSync(target)||!fs.statSync(target).isFile()){res.writeHead(404);res.end('File not found');return;}
    const size=fs.statSync(target).size;
    const headers={'Content-Type':types[ext],'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Accept-Ranges':'bytes'};
    const range=req.headers.range?.match(/^bytes=(\d*)-(\d*)$/);
    let start=0,end=size-1,status=200;
    if(range){start=range[1]?Number(range[1]):0;end=range[2]?Math.min(Number(range[2]),size-1):size-1;if(start> end||start>=size){res.writeHead(416,{'Content-Range':`bytes */${size}`});res.end();return;}status=206;headers['Content-Range']=`bytes ${start}-${end}/${size}`;}
    headers['Content-Length']=end-start+1;
    res.writeHead(status,headers);
    if(req.method==='HEAD'){res.end();return;}
    fs.createReadStream(target,{start,end}).pipe(res);
  }catch{res.writeHead(400);res.end('Invalid request');}
});
server.listen(port,'127.0.0.1',()=>console.log(`Film review: http://127.0.0.1:${port}/animatic-review.html`));
