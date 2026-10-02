"""Export the canonical learning guide as an offline, searchable HTML reader.

Requires Python markdown and beautifulsoup4. Screenshots are embedded without
modification; no network or secret access is needed to build the document.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
from pathlib import Path

import markdown
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/PROJECT_LEARNING_GUIDE.md"

STYLE = r"""
:root{--bg:#f3f5f8;--paper:#fff;--ink:#172338;--muted:#53647c;--line:#dbe3ee;--accent:#1757a6}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:28px}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.85 'Segoe UI','Microsoft YaHei',sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}button,input{font:inherit}button{cursor:pointer}
aside{position:fixed;inset:0 auto 0 0;width:290px;background:#101d31;color:#d8e3f4;padding:26px 21px;display:flex;flex-direction:column;gap:16px}
.brand{font-size:21px;font-weight:750;letter-spacing:1px}.edition{font-size:12px;color:#aabbd4}.search{width:100%;padding:10px 12px;border:1px solid #53647c;border-radius:6px;background:#1c2d46;color:#fff}.search::placeholder{color:#b6c4d8}
#search-status{font-size:12px;color:#b6c4d8;min-height:23px}nav{overflow:auto;flex:1}nav a{display:block;color:#c7d7ed;padding:8px 9px;font-size:13px;line-height:1.5;border-left:2px solid transparent}nav a.active{background:#203958;color:white;border-color:#68bbff}nav a:hover{background:#203958;text-decoration:none}
.side-actions{display:flex;gap:8px}.side-actions button{color:white;background:#213957;border:1px solid #526886;border-radius:4px;padding:6px 12px;font-size:13px}
main{margin-left:290px;padding:36px 42px 80px;max-width:1530px}.cover{background:#142844;color:white;padding:38px 42px;border-radius:10px;margin-bottom:26px}.cover small{color:#9cbce2;letter-spacing:2px}.cover h1{font-size:36px;line-height:1.35;margin:14px 0}.cover p{color:#d0dcee;max-width:790px}.stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}.stats span{border:1px solid #49617c;border-radius:4px;padding:5px 11px;font-size:13px}
article{background:var(--paper);padding:35px 40px;border:1px solid var(--line);border-radius:9px}section{margin-bottom:58px}section[hidden],tr[hidden],nav a[hidden]{display:none!important}h2{font-size:27px;line-height:1.5;margin:30px 0 22px;padding-bottom:13px;border-bottom:2px solid #bed3ec}h3{font-size:21px;line-height:1.6;margin:32px 0 15px}h4{font-size:18px}p{margin:15px 0}strong{font-weight:700}li{margin:7px 0}blockquote{margin:18px 0;padding:10px 20px;border-left:4px solid #4082c8;background:#f1f6fd;color:#3a536e}blockquote p{margin:7px 0}
table{width:100%;border-collapse:collapse;font-size:14px;margin:20px 0;table-layout:auto}th,td{padding:11px 13px;border:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3fa;font-weight:650}tr:nth-child(even){background:#fafbfd}td code{font-size:12px;white-space:normal;overflow-wrap:anywhere}pre{background:#132238;color:#e6efff;border-radius:7px;padding:20px;overflow:auto;line-height:1.7;font-size:13px}code{font-family:Consolas,'Cascadia Code',monospace;font-size:.9em;background:#edf2f8;border-radius:3px;padding:2px 4px}pre code{padding:0;background:transparent;color:inherit}
code,footer,.source-note{overflow-wrap:anywhere}pre code{overflow-wrap:normal}figure{margin:25px 0 34px}figure img{width:100%;height:auto;border:1px solid #cbd7e6;border-radius:5px;cursor:zoom-in;display:block}figcaption{color:var(--muted);font-size:13px;padding:9px 0;border-bottom:1px solid #e6ebf2}.intro{color:var(--muted)}.toc{display:none}.source-note{font-size:13px;color:var(--muted)}#empty{padding:30px;background:#fff0d6;border:1px solid #e6c282}
dialog{padding:0;border:0;background:#0c1828;color:white;border-radius:8px;width:95vw;max-width:none;height:94vh;max-height:none}dialog::backdrop{background:#07101bde}.dialog-toolbar{height:58px;display:flex;align-items:center;gap:12px;padding:8px 18px}.dialog-toolbar span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.dialog-toolbar button{background:#243c5b;color:white;border:1px solid #657e9a;border-radius:4px;padding:4px 12px}.zoom-area{overflow:auto;height:calc(100% - 58px);display:grid;place-items:start center;padding:10px}.zoom-area img{max-width:100%;height:auto}.zoom-area.original{display:block}.zoom-area.original img{max-width:none;width:auto}footer{color:var(--muted);font-size:13px;margin-top:24px}
@media(max-width:1000px){aside{width:230px;padding:20px 14px}main{margin-left:230px;padding:22px}article{padding:20px}.cover{padding:26px}.cover h1{font-size:29px}table{font-size:13px}th,td{padding:8px}}
@media(max-width:700px){aside{position:relative;width:auto;max-height:none;padding:18px}nav{max-height:240px;display:none}aside.expanded nav{display:block}.brand{font-size:18px}.edition{display:none}main{margin:0;padding:12px}article{padding:18px 13px}.cover{padding:23px}.cover h1{font-size:26px}h2{font-size:23px}h3{font-size:19px}body{font-size:15px}.table-wrap{overflow:auto}table{min-width:600px}figure{margin:20px 0}pre{font-size:12px}}
@media print{aside,.cover,.source-note,dialog,#empty{display:none!important}main{margin:0;padding:0;max-width:none}article{padding:0;border:0}section[hidden],tr[hidden]{display:revert!important}body{font-size:10pt;background:white}h2{break-before:page;font-size:18pt}h3{font-size:13pt;break-after:avoid}pre{white-space:pre-wrap;color:black;background:#f1f3f5}tr,figure{break-inside:avoid}figure img{max-height:190mm;object-fit:contain}a{color:inherit}table{font-size:8pt}th,td{padding:5px}.table-wrap{overflow:visible}}
"""

SCRIPT = r"""
const input=document.querySelector('#search');
const sections=[...document.querySelectorAll('article>section')];
const links=[...document.querySelectorAll('nav a')];
const sourceText=new Map(sections.map(s=>[s,s.innerText.toLowerCase()]));
function filter(){
 const q=input.value.trim().toLowerCase(); let matches=0;
 sections.forEach(s=>{
  const show=!q || sourceText.get(s).includes(q); s.hidden=!show;
  const a=links.find(a=>a.hash==='#'+s.id); if(a) a.hidden=!show;
  if(show) matches++;
  s.querySelectorAll('table').forEach(t=>{
   const heading=(t.closest('section').querySelector('h2')?.innerText || '').toLowerCase();
   const rows=[...t.querySelectorAll('tbody tr')];
   rows.forEach(row=>row.hidden=!!q && !heading.includes(q) && !row.innerText.toLowerCase().includes(q));
   t.closest('.table-wrap').hidden=!!q && rows.length>0 && rows.every(row=>row.hidden);
  });
  if(s===sections.at(-1))s.querySelectorAll('h3').forEach(h=>{
   let node=h.nextElementSibling;while(node&&!node.classList.contains('table-wrap')&&node.tagName!=='H3')node=node.nextElementSibling;
   h.hidden=!!q && !!node?.classList.contains('table-wrap') && node.hidden;
  });
 });
 document.querySelector('#search-status').textContent=q?`找到 ${matches} 个相关章节；表格只展示匹配行`:'按 / 搜索全文、按钮或文件名';
 document.querySelector('#empty').hidden=matches!==0;
}
input.addEventListener('input',filter);
document.querySelector('#clear').onclick=()=>{input.value='';filter();input.focus()};
document.querySelector('#menu').onclick=()=>document.querySelector('aside').classList.toggle('expanded');
document.querySelector('#print').onclick=()=>window.print();
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();input.focus()}if(e.key==='Escape'&&document.activeElement===input){input.value='';filter()}});
const dialog=document.querySelector('dialog'), fullImage=dialog.querySelector('img'), area=dialog.querySelector('.zoom-area');
document.querySelectorAll('figure img').forEach(img=>{
 img.tabIndex=0; img.setAttribute('role','button'); img.setAttribute('aria-label','放大：'+img.alt);
 const open=()=>{fullImage.src=img.src;fullImage.alt=img.alt;dialog.querySelector('span').textContent=img.alt;area.classList.remove('original');dialog.showModal()};
 img.onclick=open;img.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}};
});
dialog.querySelector('#close-image').onclick=()=>dialog.close();
dialog.querySelector('#original').onclick=()=>area.classList.toggle('original');
dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close()});
const observer=new IntersectionObserver(entries=>{const e=entries.find(e=>e.isIntersecting);if(!e)return;links.forEach(a=>a.classList.toggle('active',a.hash==='#'+e.target.id))},{rootMargin:'-10% 0px -65% 0px'});
sections.forEach(s=>observer.observe(s));filter();
"""


def build(output: Path) -> dict:
    text = SOURCE.read_text(encoding="utf-8")
    html = markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"], output_format="html5")
    soup = BeautifulSoup(html, "html.parser")
    missing = []
    image_paths = []
    for img in soup.find_all("img"):
        name = img["src"]
        if name.startswith(("http:", "https:", "data:")):
            raise ValueError("The offline guide requires local images: " + name)
        image_path = (SOURCE.parent / name).resolve()
        if not image_path.is_relative_to(ROOT) or not image_path.is_file():
            missing.append(name)
            continue
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        img["src"] = f"data:{mime};base64," + base64.b64encode(image_path.read_bytes()).decode()
        img["loading"] = "lazy"
        image_paths.append(image_path.relative_to(ROOT).as_posix())
        parent = img.parent
        if parent.name == "a":
            parent.replace_with(img)
        parent = img.parent
        figure = soup.new_tag("figure")
        img.replace_with(figure)
        figure.append(img)
        caption = soup.new_tag("figcaption")
        caption.string = (img.get("alt") or image_path.stem) + " · 点击放大 / Esc 关闭"
        figure.append(caption)
        if parent.name == "p":
            parent.unwrap()
    if missing:
        raise ValueError("Missing screenshot files: " + ", ".join(missing))
    ids = {n.get('id') for n in soup.find_all(id=True)}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#"):
            if href[1:] not in ids:
                missing.append(href)
        elif not re.match(r"^[a-z]+:", href):
            raw, _, anchor = href.partition("#")
            local = (SOURCE.parent / raw).resolve()
            if not local.exists():
                missing.append(href)
            else:
                a["href"] = local.as_uri() + ("#" + anchor if anchor else "")
        else:
            a["rel"] = "noopener noreferrer"
    # File names in the generated index and teaching text link to actual source.
    source_links = 0
    for code in list(soup.find_all('code')):
        if code.parent.name in ('pre', 'a'):
            continue
        name = code.get_text()
        candidate = (ROOT / name).resolve()
        if '/' in name and candidate.is_relative_to(ROOT) and candidate.is_file():
            a = soup.new_tag('a', href=candidate.as_uri())
            code.wrap(a)
            source_links += 1
    for table in list(soup.find_all('table')):
        wrap = soup.new_tag('div', attrs={'class':'table-wrap'})
        table.wrap(wrap)
    intro = soup.new_tag('section', id='reading-start')
    intro['class']='intro'
    article = soup.new_tag('article')
    section = intro
    article.append(section)
    navigation = []
    for node in list(soup.contents):
        if getattr(node, 'name', None) == 'h1':
            continue
        if getattr(node, 'name', None) == 'h2':
            section = soup.new_tag('section', id='chapter-' + str(len(navigation)))
            navigation.append((section['id'],node.get_text()))
            article.append(section)
        section.append(node.extract())
    from html import escape
    nav = ''.join(f'<a href="#{key}">{escape(label)}</a>' for key,label in navigation)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    body = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mini-Drop 深入学习与演示手册</title><style>{STYLE}</style></head><body>
    <aside><div class="brand">MINI-DROP / 项目教材</div><div class="edition">2026-09-10 · 云端版本 20260909T174951Z</div><input id="search" class="search" type="search" placeholder="搜索按钮、知识点或文件名" aria-label="搜索教材全文"><div id="search-status" aria-live="polite"></div><nav aria-label="章节目录">{nav}</nav><div class="side-actions"><button id="menu">目录</button><button id="clear">清空搜索</button><button id="print">打印</button></div></aside>
    <main><header class="cover"><small>从实际页面开始，沿着证据读懂源码</small><h1>Mini-Drop<br>深入学习与演示手册</h1><p>页面操作、基础采集、AI 诊断、源码结构、前置知识与面试问答。截图取自真实云端，条件操作按当前代码解释。</p><div class="stats"><span>{len(image_paths)} 张插图</span><span>{len(navigation)} 个主章节</span><span>40 个专题面试问答</span><span>可离线阅读 · 图片可放大</span></div></header>
    <p class="source-note">正文由 docs/PROJECT_LEARNING_GUIDE.md 生成。源码链接指向本机工作区；把此 HTML 单独复制到其他电脑后，正文与图片仍完整可读，源码链接需要对应仓库。</p><div id="empty" hidden>没有找到匹配内容。可尝试“工具”“租约”“RAG”或文件名的一部分。</div>{article}<footer>源文档 SHA-256：{source_hash}<br>本文件为阅读副本；修改教材请回到 Markdown 源文档并重新生成。</footer></main>
    <dialog aria-label="截图放大"><div class="dialog-toolbar"><span></span><button id="original">原尺寸 / 适应</button><button id="close-image">关闭</button></div><div class="zoom-area"><img alt=""></div></dialog><script>{SCRIPT}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body,encoding='utf-8')
    report = {'source':str(SOURCE),'source_sha256':source_hash,'output':str(output),'chapters':len(navigation),
        'images':image_paths,'image_count':len(image_paths),'source_links':source_links,'broken_links':missing,
        'html_bytes':output.stat().st_size,'passed':not missing}
    output.with_suffix('.build.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if missing:
        raise ValueError('Broken document links: '+', '.join(missing))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/learning-guide/Mini-Drop深入学习与演示手册.html')
    print(json.dumps(build(parser.parse_args().output.resolve()),ensure_ascii=False,indent=2))
