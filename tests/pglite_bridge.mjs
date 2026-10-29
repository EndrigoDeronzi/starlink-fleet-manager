const { PGlite } = await import(process.argv[2] || '@electric-sql/pglite');
import readline from 'node:readline';
const db=new PGlite();
for await(const line of readline.createInterface({input:process.stdin})){
 try{const m=JSON.parse(line);const result=m.script?await db.exec(m.sql):await db.query(m.sql,m.params||[]);process.stdout.write(JSON.stringify({ok:true,result})+'\n');}
 catch(e){process.stdout.write(JSON.stringify({ok:false,error:e.message,code:e.code})+'\n');}
}
await db.close();
