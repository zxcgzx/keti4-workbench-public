import {compileFromFile} from 'json-schema-to-typescript';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const schema=fileURLToPath(new URL('../../backend/app/spec/model_schema.json',import.meta.url));
const output=fileURLToPath(new URL('../src/types/generated.ts',import.meta.url));
const ts=await compileFromFile(schema,{bannerComment:'/* 自动生成：来源为后端 Pydantic ModelSpec JSON Schema，请运行 npm run typegen 更新。 */',additionalProperties:false});
await mkdir(fileURLToPath(new URL('../src/types/',import.meta.url)),{recursive:true});
await writeFile(output,ts,'utf8');
console.log('Generated src/types/generated.ts from Pydantic JSON Schema.');
