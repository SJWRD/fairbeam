import regularUrl from "../assets/fonts/IBMPlexSans-Regular.woff?url";
import semiboldUrl from "../assets/fonts/IBMPlexSans-SemiBold.woff?url";
import { toBase64 } from "../drawing/font";
import { saveDownload } from "../lib/download";
import { downloadToast } from "../lib/toast";
import { exportNotice } from "./exportContext";
import { titledSvg } from "./figureTitle";
import { decimalComma, t } from "../i18n";
const shown = (node: Element) => {
  const box=node.getBoundingClientRect(), visibility=getComputedStyle(node).visibility;
  return box.width>0 && box.height>0 && !node.closest("[hidden],[inert]") && visibility!=="hidden" && visibility!=="collapse";
};
export function visibleFigureSvgs(root:HTMLElement|null):SVGSVGElement[] {
  return root ? [...root.querySelectorAll<SVGSVGElement>(".chart svg")].filter(shown) : [];
}
/** Snapshot the actual plotted SVGs, including comparison traces and current quantity choices.
 * Resolve CSS roles in the light theme for a standalone figure on white paper. */
export function visibleFigureSvg(root:HTMLElement):string|null {
  const charts=visibleFigureSvgs(root);if(!charts.length)return null;
  const rect=root.getBoundingClientRect();
  const chartParents=new Set(charts.map(svg=>svg.closest(".chart")));
  const labels=[...root.querySelectorAll<HTMLElement>(".chart .legend-item,.chart .legend-note,.chart .chart-note")]
    .filter(node=>chartParents.has(node.closest(".chart")) && shown(node));
  const boxes=[...charts,...labels].map(node=>node.getBoundingClientRect());
  const width=Math.max(...boxes.map(box=>box.right-rect.left));
  const height=Math.max(...boxes.map(box=>box.bottom-rect.top));
  // Decimal punctuation is a display preference. Engineering figures keep decimal points,
  // but a point-decimal UI's commas (for example an English thousands separator) stay intact.
  const comma=decimalComma(), plain=(value:string)=>comma ? value.replace(/(\d),(?=\d)/g,"$1.") : value;
  const out=document.createElementNS("http://www.w3.org/2000/svg","svg");
  out.setAttribute("xmlns","http://www.w3.org/2000/svg");out.setAttribute("viewBox",`0 0 ${width} ${height}`);
  out.setAttribute("width",`${width/96*25.4}mm`);out.setAttribute("height",`${height/96*25.4}mm`);
  const paper=document.createElementNS("http://www.w3.org/2000/svg","rect");paper.setAttribute("width",String(width));paper.setAttribute("height",String(height));paper.setAttribute("fill","#fff");out.append(paper);
  // Read the light palette synchronously, then restore before the browser paints or any async work.
  const html=document.documentElement, theme=html.getAttribute("data-theme");
  html.setAttribute("data-theme","light");
  try {
    for(const svg of charts) {
      const clone=svg.cloneNode(true) as SVGSVGElement;
      const original=[svg,...svg.querySelectorAll<SVGElement>("*")],copies=[clone,...clone.querySelectorAll<SVGElement>("*")];
      original.forEach((node,k)=>{const style=getComputedStyle(node);for(const key of ["fill","fill-opacity","stroke","stroke-width","stroke-opacity","stroke-dasharray","font-family","font-size","font-weight","text-anchor","dominant-baseline","opacity","paint-order","stroke-linecap","stroke-linejoin"])copies[k].style.setProperty(key,style.getPropertyValue(key));});
      const box=svg.getBoundingClientRect();clone.setAttribute("x",String(box.left-rect.left));clone.setAttribute("y",String(box.top-rect.top));clone.setAttribute("width",String(box.width));clone.setAttribute("height",String(box.height));
      // Include accessible titles within text too. Update nodes without flattening tspans.
      const words=document.createTreeWalker(clone,NodeFilter.SHOW_TEXT);
      for(let node=words.nextNode();node;node=words.nextNode()) {
        const parent=node.parentElement;
        if(parent?.closest("[data-figure-raw]"))continue;
        const explicit=parent?.closest("[data-figure-text]")?.getAttribute("data-figure-text");
        if(explicit!==null && explicit!==undefined)node.textContent=explicit;
        else if(parent?.closest("text,tspan"))node.textContent=plain(node.textContent ?? "");
      }
      out.append(clone);
    }
    // HTML legends and notes are not part of the chart SVG. Preserve their actual wrapped lines
    // and positions, including the Smith reference note below the SVG and the polar radial units.
    for(const item of labels) {
      const key=item.querySelector<HTMLElement>(".legend-key");
      const text=document.createElementNS("http://www.w3.org/2000/svg","text");
      const style=getComputedStyle(item);
      text.setAttribute("dominant-baseline","middle");text.setAttribute("fill",style.color);
      text.setAttribute("font-family",style.fontFamily);text.setAttribute("font-size",style.fontSize);
      text.setAttribute("font-weight",style.fontWeight);
      const lines:{left:number;top:number;height:number;value:string}[]=[];
      const walker=document.createTreeWalker(item,NodeFilter.SHOW_TEXT), range=document.createRange();
      // Legend labels can contain arbitrary run/reference names. Only the chart that created
      // a localized numeric suffix can supply its export spelling; never rewrite the raw name.
      const literal=item.classList.contains("legend-item"), explicit=item.dataset.figureText;
      const nodes:{node:Node;start:number;end:number}[]=[];
      let value="";
      for(let node=walker.nextNode();node;node=walker.nextNode()) {
        if(key?.contains(node))continue;
        const parent=node.parentElement;
        if(!parent || parent.closest("[hidden],[inert]"))continue;
        const visibility=getComputedStyle(parent).visibility;
        if(visibility==="hidden" || visibility==="collapse")continue;
        let rendered=true;
        for(let ancestor:Element|null=parent;ancestor && ancestor!==item.parentElement;ancestor=ancestor.parentElement) {
          if(getComputedStyle(ancestor).display==="none") {rendered=false;break;}
        }
        if(!rendered)continue; // display:contents and visible zero-advance text still participate.
        const part=node.textContent ?? "";
        if(part.length)nodes.push({node,start:value.length,end:value.length+part.length});
        value+=part;
      }
      // Measure whole graphemes, including those split across DOM text nodes. Keep the original
      // UTF-16 spelling and offsets: normalization would change raw names and suffix positions.
      let first=0,last=0,pending="";
      for(const {segment,index} of new Intl.Segmenter(undefined,{granularity:"grapheme"}).segment(value)) {
        const end=index+segment.length;
        while(nodes[first].end<=index)first++;
        while(nodes[last].end<end)last++;
        range.setStart(nodes[first].node,index-nodes[first].start);range.setEnd(nodes[last].node,end-nodes[last].start);
        const box=range.getBoundingClientRect();
        const replacement=explicit?.slice(index,end);
        const exported=replacement===undefined ? segment : replacement+segment.slice(replacement.length);
        if(!box.width || !box.height) {
          // Zero-advance marks/controls and collapsed wrap spaces still belong to the raw label.
          if(lines.length)lines[lines.length-1].value+=exported;else pending+=exported;
          continue;
        }
        let line=lines.at(-1);
        if(!line || Math.abs(line.top-box.top)>1)lines.push(line={left:box.left,top:box.top,height:box.height,value:""});
        line.value+=pending+exported;pending="";
      }
      if(pending) {const box=item.getBoundingClientRect();lines.push({left:box.left,top:box.top,height:box.height,value:pending});}
      for(const line of lines) {
        const span=document.createElementNS("http://www.w3.org/2000/svg","tspan");
        span.setAttribute("x",String(line.left-rect.left));span.setAttribute("y",String(line.top+line.height/2-rect.top));
        span.textContent=literal ? line.value : plain(line.value);text.append(span);
      }
      out.append(text);
      if(key) {
        const r=key.getBoundingClientRect(), st=getComputedStyle(key);
        const color=st.backgroundImage.match(/rgba?\([^)]+\)/)?.[0] ?? st.backgroundColor;
        if(key.classList.contains("legend-point")) {
          const symbol=document.createElementNS("http://www.w3.org/2000/svg",key.classList.contains("legend-point-diamond") ? "path" : "circle");
          const cx=r.left+r.width/2-rect.left,cy=r.top+r.height/2-rect.top;
          if(symbol.tagName==="circle") {symbol.setAttribute("cx",String(cx));symbol.setAttribute("cy",String(cy));symbol.setAttribute("r","4");}
          else symbol.setAttribute("d",`M${cx},${cy-4}l4,4l-4,4l-4,-4Z`);
          symbol.setAttribute("fill",color);out.append(symbol);continue;
        }
        const line=document.createElementNS("http://www.w3.org/2000/svg","line");
        line.setAttribute("x1",String(r.left-rect.left));line.setAttribute("x2",String(r.right-rect.left));
        line.setAttribute("y1",String(r.top+r.height/2-rect.top));line.setAttribute("y2",String(r.top+r.height/2-rect.top));
        line.setAttribute("stroke",color);line.setAttribute("stroke-width","2");
        if(key.dataset.dash)line.setAttribute("stroke-dasharray",key.dataset.dash);
        else if(st.backgroundImage!=="none")line.setAttribute("stroke-dasharray","4 3");
        out.append(line);
      }
    }
  } finally {if(theme===null)html.removeAttribute("data-theme");else html.setAttribute("data-theme",theme);}
  return new XMLSerializer().serializeToString(out);
}
let fontFaces:Promise<string>|undefined;
function embeddedFontFaces():Promise<string> {
  return fontFaces ??= Promise.all([regularUrl,semiboldUrl].map(async(url,k)=>{
    const response=await fetch(url);if(!response.ok)throw new Error(`Font loading failed (${response.status})`);
    const bytes=new Uint8Array(await response.arrayBuffer());
    return `@font-face{font-family:'IBM Plex Sans';font-weight:${k===0?400:600};src:url(data:font/woff;base64,${toBase64(bytes)}) format('woff')}`;
  })).then(faces=>faces.join(""));
}
export async function saveVisibleFigure(root:HTMLElement|null,base:string,format:"svg"|"png",title?:string) {
  let svg=root && visibleFigureSvg(root);if(!svg){exportNotice(t("contextExport.noChart"));return;}
  if(title)svg=titledSvg(svg,title);
  if(format==="svg")svg=svg.replace(/(<svg[^>]*>)/,`$1<style>${await embeddedFontFaces()}</style>`);
  const name=`${base.replace(/[^a-z0-9._+-]+/gi,"_")}.${format}`;
  const data=format==="svg" ? svg : await (await import("../drawing/render")).svgToPng(svg,300);
  downloadToast(await saveDownload(name,data,format==="svg"?"image/svg+xml":"image/png"));
}
