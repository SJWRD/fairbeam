import { onCleanup, onMount } from "solid-js";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { ResearchResult } from "../runner/api";
import { t } from "../i18n";

/** Normalized field radius; the numerical directivity stays in the accompanying cuts. */
export default function ElmerFarfieldView(props:{field:NonNullable<ResearchResult["farfield"]>}){
  let host:HTMLDivElement|undefined;
  onMount(()=>{
    const canvas=document.createElement("canvas");canvas.tabIndex=0;canvas.setAttribute("aria-label",t("elmerAntenna.farfield"));
    const renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:true});
    renderer.setPixelRatio(Math.min(devicePixelRatio,2));host!.append(canvas);
    const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,1,.01,100);camera.position.set(2.8,2.3,2.4);camera.up.set(0,0,1);
    const controls=new OrbitControls(camera,canvas);controls.enableDamping=false;controls.listenToKeyEvents(canvas);
    const ff=props.field,rows=ff.theta_deg.length,cols=ff.phi_deg.length;
    const peak=Math.max(...ff.directivity.flat());const positions:number[]=[],colors:number[]=[],indices:number[]=[];
    for(let i=0;i<rows;i++)for(let j=0;j<=cols;j++){
      const value=ff.directivity[i][j%cols],r=Math.sqrt(value/peak),theta=ff.theta_deg[i]*Math.PI/180,phi=2*Math.PI*j/cols;
      positions.push(r*Math.sin(theta)*Math.cos(phi),r*Math.sin(theta)*Math.sin(phi),r*Math.cos(theta));
      const color=new THREE.Color().setHSL(.62*(1-r),.75,.48);colors.push(color.r,color.g,color.b);
      if(i<rows-1 && j<cols){const a=i*(cols+1)+j,b=a+cols+1;indices.push(a,b,a+1,a+1,b,b+1);}
    }
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));geometry.setIndex(indices);geometry.computeVertexNormals();
    const material=new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide});scene.add(new THREE.Mesh(geometry,material));
    const axes=new THREE.AxesHelper(1.2);scene.add(axes);
    const render=()=>renderer.render(scene,camera);
    const resize=()=>{const {width,height}=host!.getBoundingClientRect();renderer.setSize(width,height);camera.aspect=width/Math.max(height,1);camera.updateProjectionMatrix();render();};
    controls.addEventListener('change',render);const observer=new ResizeObserver(resize);observer.observe(host!);controls.update();resize();
    onCleanup(()=>{observer.disconnect();controls.dispose();geometry.dispose();material.dispose();axes.dispose();renderer.dispose();canvas.remove();});
  });
  return <div><div ref={host} class="elmer-farfield-view"/><p class="note">{t("elmerAntenna.rotate")}</p></div>;
}
