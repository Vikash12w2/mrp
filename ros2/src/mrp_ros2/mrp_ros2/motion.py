import math

def interpolate(start, end, fraction):
    fraction = min(1.0, max(0.0, fraction))
    return {k: tuple(a+(b-a)*fraction for a,b in zip(start[k],p)) for k,p in end.items()}


def validate_segments(start, end, obstacles, clearance=.68):
    def distance(a,b,c,d):
        rx,ry=a[0]-c[0],a[1]-c[1]
        vx,vy=b[0]-a[0]-d[0]+c[0],b[1]-a[1]-d[1]+c[1]
        vv=vx*vx+vy*vy
        t=min(1.,max(0.,-(rx*vx+ry*vy)/vv)) if vv else 0.
        return math.hypot(rx+t*vx,ry+t*vy)
    keys=list(end)
    for i,k in enumerate(keys):
        for obstacle in obstacles:
            if distance(start[k],end[k],obstacle,obstacle)<clearance:
                raise ValueError(f'{k}: smooth segment passes too close to an obstacle')
        for other in keys[i+1:]:
            if (k.startswith('r') or other.startswith('r')) and distance(start[k],end[k],start[other],end[other])<clearance:
                raise ValueError(f'{k}/{other}: smooth segments intersect')
