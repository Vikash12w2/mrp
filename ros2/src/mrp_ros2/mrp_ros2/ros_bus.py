import importlib
import json
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import fields, is_dataclass
from itertools import count
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String


class Codec:
    def __init__(self, package):
        self.types = {}
        for module, names in {'model': ['Prim','WorkerSpec','Instance'],
                              'planner':['GroupPlan'], 'twoshot':['Result','Ext','Options'],
                              'deployment':['SetupPath']}.items():
            source = importlib.import_module(package+'.'+module)
            for name in names:
                self.types[module+'.'+name] = getattr(source,name)

    def encode(self, obj):
        if obj is None or type(obj) in (bool,int,float,str):
            return obj
        if is_dataclass(obj):
            name=next((k for k,v in self.types.items() if type(obj) is v),None)
            if name is None:
                raise ValueError(f'Unsupported transport class {type(obj)}')
            return {'type':name,'fields':{f.name:self.encode(getattr(obj,f.name)) for f in fields(obj)}}
        if isinstance(obj,dict):
            return {'type':'dict','items':[[self.encode(k),self.encode(v)] for k,v in obj.items()]}
        if type(obj) in (list,tuple,set,frozenset):
            return {'type':type(obj).__name__,'items':[self.encode(v) for v in obj]}
        raise ValueError(f'Unsupported transport value {type(obj)}')

    def decode(self, obj):
        if obj is None or type(obj) in (bool,int,float,str):
            return obj
        tag=obj['type']
        if tag=='dict':
            return {self.decode(k):self.decode(v) for k,v in obj['items']}
        if tag in ('list','tuple','set','frozenset'):
            return {'list':list,'tuple':tuple,'set':set,'frozenset':frozenset}[tag](self.decode(v) for v in obj['items'])
        if tag not in self.types:
            raise ValueError('Unknown transport type')
        return self.types[tag](**{k:self.decode(v) for k,v in obj['fields'].items()})


class RosBus:
    def __init__(self, anchors, R_c, retries=3, loss=None, node=None):
        if node is None:
            raise ValueError('RosBus needs its owning planner node')
        self.node=node; self.anchors=anchors; self.R_c=R_c; self.retries=retries; self.loss=loss
        self.inbox=defaultdict(list); self.stats=Counter(); self.unreachable=[]; self._seq=count(1)
        source=importlib.import_module(node.mrp_module+'.bus')
        self.Msg=source.Msg; self.ack_kinds=source.ACK_KINDS; self.periodic=source.PERIODIC_KINDS
        self.codec=Codec(node.mrp_module)
        self.session=uuid.uuid4().hex
        self.seen=set(); self.acked=set(); self.pubs={}; self.ack_pubs={}; self.nodes={}
        self.executor=SingleThreadedExecutor(context=node.context)
        for rid in anchors:
            endpoint=Node(f'mrp_negotiation_robot_{rid}_{self.session[:8]}',context=node.context,
                          use_global_arguments=False)
            self.nodes[rid]=endpoint; self.executor.add_node(endpoint)
            base=f'/mrp/negotiation/robot_{rid}'
            for peer in anchors:
                if peer == rid:
                    continue
                peer_base=f'/mrp/negotiation/robot_{peer}'
                self.pubs[rid,peer]=endpoint.create_publisher(String,peer_base+'/inbox',10)
                self.ack_pubs[rid,peer]=endpoint.create_publisher(String,peer_base+'/ack',10)
            endpoint.create_subscription(String,base+'/inbox',lambda m,d=rid:self._receive(d,m),10)
            endpoint.create_subscription(String,base+'/ack',lambda m,s=rid:self._ack(s,m),10)
        if not hasattr(node,'transport_buses'):
            node.transport_buses=[]
        node.transport_buses.append(self)

    def close(self):
        self.executor.shutdown()
        for endpoint in self.nodes.values():
            endpoint.destroy_node()
        self.nodes.clear()

    def in_range(self,a,b):
        return a!=b and max(abs(x-y) for x,y in zip(self.anchors[a],self.anchors[b]))<=self.R_c

    def _ack(self,src,msg):
        try:
            e=json.loads(msg.data)
            if e['session']==self.session and e['src']==src:
                self.acked.add((src,e['dst'],e['seq']))
        except (ValueError,KeyError,TypeError):
            pass

    def _receive(self,dst,msg):
        try:
            e=json.loads(msg.data)
            if e['session']!=self.session or e['dst']!=dst or not self.in_range(e['src'],dst):
                return
            key=(e['src'],dst,e['seq'])
            if key not in self.seen:
                payload=self.codec.decode(e['payload'])
                self.inbox[dst].append(self.Msg(e['src'],dst,e['kind'],payload,e['seq']))
                self.seen.add(key)
            if e['kind'] in self.ack_kinds:
                self.ack_pubs[dst,e['src']].publish(String(data=json.dumps(dict(session=self.session,src=e['src'],dst=dst,seq=e['seq']))))
        except (ValueError,KeyError,TypeError) as exc:
            self.node.get_logger().error(f'Invalid negotiation envelope: {exc}')

    def send(self,src,dst,kind,payload):
        if not self.in_range(src,dst):
            self.unreachable.append((src,dst,kind)); return False
        seq=next(self._seq); key=(src,dst,seq)
        attempts=1+(self.retries if kind in self.ack_kinds else 0)
        msg=String(data=json.dumps(dict(session=self.session,src=src,dst=dst,kind=kind,seq=seq,payload=self.codec.encode(payload))))
        for attempt in range(attempts):
            if self.loss and self.loss(src,dst,kind,seq,attempt):
                self.stats['retry']+=1; continue
            self.pubs[src,dst].publish(msg)
            deadline=time.monotonic()+1.0
            received=self.acked if kind in self.ack_kinds else self.seen
            while key not in received and time.monotonic()<deadline and rclpy.ok(context=self.node.context):
                self.executor.spin_once(timeout_sec=.01)
            if key in received:
                self.stats[kind]+=1
                if kind in self.ack_kinds:
                    self.stats['ack']+=1
                return True
            self.stats['retry']+=1
        if kind not in self.periodic:
            self.unreachable.append((src,dst,kind))
        else:
            self.stats['periodic_drop']+=1
        return False

    def broadcast(self,src,kind,payload):
        for dst in sorted(self.anchors):
            if self.in_range(src,dst):self.send(src,dst,kind,payload)

    def drain(self,rid,kind):
        keep,out=[],[]
        for msg in self.inbox[rid]:
            (out if msg.kind==kind else keep).append(msg)
        self.inbox[rid]=keep
        return sorted(out,key=lambda m:(m.src,m.seq))
