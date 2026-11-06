import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';

const project = path.resolve('E:/tx-drop');
const web = path.join(project, 'web/node_modules');
const { unified } = await import(pathToFileURL(path.join(web, 'unified/index.js')));
const { default: parse } = await import(pathToFileURL(path.join(web, 'remark-parse/index.js')));
const { default: gfm } = await import(pathToFileURL(path.join(web, 'remark-gfm/index.js')));
const out = path.join(project, 'docs/PROJECT_INTERVIEW_HANDBOOK.md');
const bytes = await fs.readFile(out);
const markdown = bytes.toString('utf8');
const tree = unified().use(parse).use(gfm).parse(markdown);
const report = {
  artifact: { path: out, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), bytes: bytes.length },
  checked_on: '2026-10-25',
  scope: 'Static Markdown/source-reference and structure validation only; no application tests, model calls or collection.',
  checks: [], errors: [], references: [], statistics: { chapters: 0, subsections: 0, code_blocks: 0, tables: 0, han_excluding_fenced_code: 0 },
};
const nodes = [];
function visit(node) { nodes.push(node); for (const child of node.children || []) visit(child); }
visit(tree);
const anchors = new Set([...markdown.matchAll(/<a id="([^"]+)"><\/a>/g)].map(m => m[1]));
const links = nodes.filter(n => n.type === 'link' || n.type === 'image');
let prose = '';
for (const node of nodes) {
  if (node.type === 'heading') {
    if (node.depth === 2) report.statistics.chapters++;
    if (node.depth === 3) report.statistics.subsections++;
  }
  if (node.type === 'table') report.statistics.tables++;
  if (node.type === 'code') report.statistics.code_blocks++;
  if (node.type === 'text' || node.type === 'inlineCode') prose += node.value;
}
report.statistics.han_excluding_fenced_code = (prose.match(/\p{Script=Han}/gu) || []).length;
const localFiles = new Map();
for (const link of links) {
  const url = link.url;
  if (/^(https?:|mailto:)/i.test(url)) {
    report.references.push({ target: url, kind: 'external-not-requested' }); continue;
  }
  if (url.startsWith('#')) {
    if (!anchors.has(url.slice(1))) report.errors.push({ code: 'unknown-anchor', target: url });
    report.references.push({ target: url, kind: 'chapter-anchor' }); continue;
  }
  const target = decodeURIComponent(url.split('#')[0].split('?')[0]);
  const resolved = path.resolve(path.dirname(out), target);
  try {
    const stat = await fs.stat(resolved);
    let binding;
    if (stat.isFile()) {
      const content = await fs.readFile(resolved);
      binding = {sha256:crypto.createHash('sha256').update(content).digest('hex'), bytes:content.length};
      localFiles.set(resolved, binding);
    }
    report.references.push({target:url,kind:stat.isDirectory()?'directory':'local-file',...binding});
  } catch {
    report.errors.push({ code: 'missing-local-link', target: url });
  }
}
report.statistics.distinct_linked_files = localFiles.size;
report.statistics.local_link_occurrences = report.references.filter(r=>r.kind==='local-file'||r.kind==='directory').length;
report.statistics.chapter_anchors = anchors.size;
const has = text => markdown.includes(text);
for (const [name,ok] of [
  ['one title', nodes.filter(n=>n.type==='heading'&&n.depth===1).length===1],
  ['chapter anchors', anchors.size===report.statistics.chapters],
  ['no unresolved template', !/INTERVIEW_TOC|__ATLAS_DATA__|待补充|TODO/.test(markdown)],
  ['developer and test responsibilities', has('开发职责')&&has('测试职责')],
  ['interview sessions and prompts', has('45 分钟')&&has('60 分钟')&&has('15 分钟')&&has('模拟面试提示词')],
  ['source review is not runtime test', has('没有重新执行历史性能实验')&&has('静态')],
  ['known lease and SSE boundaries', has('lease_expires_at')&&has('提交顺序')&&has('setrlimit')],
  ['agent supplementary section', has('AI 功能怎样简讲')],
]) { report.checks.push({name,passed:ok}); if(!ok)report.errors.push({code:'structure-check',name}); }
report.passed = report.errors.length===0;
const receipt=path.join(project,'.local/interview_drafts/markdown-check.json');
await fs.writeFile(receipt,JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({passed:report.passed,statistics:report.statistics,errors:report.errors},null,2));
if(!report.passed)process.exitCode=1;
