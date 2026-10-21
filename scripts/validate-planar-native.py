"""Bounded synthetic through-line controls; never an antenna benchmark.

The quasi-static references are the zero-thickness microstrip approximation
and the finite-substrate conformal CPW approximation. They are sanity bounds,
not exact full-wave standards. Preserve raw probes and never fit a correction.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time
import inspect
import numpy as np
import openEMS
from fairbeam import Simulation
from fairbeam import planar,automesh


def elliptic_k(k):
    a,b=1.,np.sqrt(1-k*k)
    for _ in range(30):a,b=(a+b)/2,np.sqrt(a*b)
    return np.pi/(2*a)


def reference(kind,w=2.,gap=.6,h=1.,er=3.):
    if kind=="microstrip":
        u=w/h
        effective=(er+1)/2+(er-1)/2/np.sqrt(1+12/u)
        impedance=(120*np.pi/(np.sqrt(effective)*(u+1.393+.667*np.log(u+1.444))))
    else:
        k=w/(w+2*gap);kp=np.sqrt(1-k*k)
        k1=np.sinh(np.pi*w/(4*h))/np.sinh(np.pi*(w+2*gap)/(4*h))
        ratio=elliptic_k(kp)/elliptic_k(k)
        effective=1+(er-1)/2*ratio*elliptic_k(k1)/elliptic_k(np.sqrt(1-k1*k1))
        impedance=30*np.pi/np.sqrt(effective)*ratio
    return float(impedance),float(effective)


def build(kind,level):
    sim=Simulation(1e9,2e9,boundaries=["PML_12"]*6,end_criteria_db=-60,max_timesteps=150000)
    sim.dielectric("Synthetic substrate",3).AddBox(start=[-60,-12,0],stop=[60,12,1],priority=0)
    metal=sim.metal("Synthetic PEC")
    metal.AddBox(start=[-6,-1,1],stop=[6,1,1],priority=5)
    if kind=="microstrip":metal.AddBox(start=[-60,-12,0],stop=[60,12,0],priority=5)
    else:
        metal.AddBox(start=[-60,-12,1],stop=[60,-1.6,1],priority=5)
        metal.AddBox(start=[-60,1.6,1],stop=[60,12,1],priority=5)
    zref,_=reference(kind)
    for number,start,stop,excited in [(1,-60,-6,True),(2,60,6,False)]:
        common=dict(conductor="Synthetic PEC",feed_shift=30 if excited else 0,
                    measurement_shift=45,reference_ohm=zref,termination="pml",excite=excited)
        if kind=="microstrip":sim.microstrip_port(number,[start,-1,1],[stop,1,0],"x","z",**common)
        else:sim.cpw_port(number,[start,-1,1],[stop,1,1],"x","y",.6,**common)
    # Integer reference/source coordinates stay on a symmetric axial cover at
    # every level. A 1/1.5**level cover interleaved an asymmetric hard user line
    # beside the observation plane, which the automatic mesher must not move.
    sim.mesh.AddLine("x",np.linspace(-60,60,120*2**level+1))
    sim.auto_mesh(cells_per_wavelength=20*1.5**level,air_cells_per_wavelength=20,
        edge_res=.4/1.5**level,dielectric_cells=int(np.ceil(6*1.5**level)),edge_rule="thirds",
        metal_cells=6,refine_features=True,pad=[0,0,75,75,75,75],max_ratio=1.35,min_cell=.02/1.5**level)
    return sim


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",required=True)
    ap.add_argument("--kind",choices=["microstrip","cpw"],required=True)
    ap.add_argument("--level",type=int,choices=range(4),default=0)
    ap.add_argument("--engine",choices=["cpu","gpu"],default="cpu")
    args=ap.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).read_bytes();(out/"generator.py").write_bytes(source)
    manifest=dict(parameters=vars(args),openems=openEMS.__version__,python=platform.python_version(),
        generator_sha256=hashlib.sha256(source).hexdigest(),status="started",
        limits=dict(impedance_relative=.05,beta_relative=.02,reflection=.05,transmission_complex=.05),
        assumptions="Lossless PEC, finite 24-mm substrate/ground width; quasi-static approximation; no fitted correction")
    manifest["source_files"]={str(Path(inspect.getfile(module))):hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest()
        for module in (planar,automesh,Simulation)}
    manifest["ports_module_sha256"]=hashlib.sha256(Path(openEMS.__file__).with_name("ports.py").read_bytes()).hexdigest()
    started=time.monotonic()
    try:
        sim=build(args.kind,args.level);sim.finalize_planar_ports()
        manifest.update(ports=sim.ports,grid=[len(sim.mesh.GetLines(j)) for j in range(3)])
        (out/"manifest.json").write_text(json.dumps(manifest,indent=2))
        stats=sim.run(str(out/"raw"),threads=4,engine=args.engine)
        f=np.linspace(1e9,2e9,201)
        a,b=sim._port_objs
        for port in (a,b):port.CalcPort(str(out/"raw"),f)
        s11=a.uf_ref/a.uf_inc;s21=b.uf_ref/a.uf_inc
        zref,effective=reference(args.kind);beta=2*np.pi*f/299792458*np.sqrt(effective)
        length=abs(sim.ports[1]["reference_plane"]-sim.ports[0]["reference_plane"])*1e-3
        expected=np.exp(-1j*beta*length)
        metrics=dict(impedance_max_relative=float(max(abs(a.native_line_z/zref-1))),
            beta_max_relative=float(max(abs(a.beta/beta-1))),reflection_max=float(max(abs(s11))),
            transmission_max_complex_error=float(max(abs(s21-expected))),
            max_power_excess=float(max(abs(s11)**2+abs(s21)**2-1)))
        passed=(stats.get("converged") is True and all(np.isfinite(list(metrics.values()))) and
            metrics["impedance_max_relative"]<=.05 and metrics["beta_max_relative"]<=.02 and
            metrics["reflection_max"]<=.05 and metrics["transmission_max_complex_error"]<=.05)
        np.savetxt(out/"curves.csv",np.column_stack([f,s11.real,s11.imag,s21.real,s21.imag,
            a.native_line_z.real,a.native_line_z.imag,a.beta.real,a.beta.imag]),delimiter=",",
            header="frequency_hz,s11_re,s11_im,s21_re,s21_im,native_z_re,native_z_im,beta_re,beta_im",comments="")
        manifest.update(status="completed",passed=bool(passed),metrics=metrics,run=stats,
                        reference=dict(z_ohm=zref,effective_epsilon=effective,length_m=length))
    except Exception as error:
        manifest.update(status="failed",passed=False,error=str(error));raise
    finally:
        manifest["wall_seconds"]=time.monotonic()-started
        (out/"manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps(dict(passed=manifest["passed"],metrics=metrics),indent=2))

if __name__=="__main__":main()
