from dataclasses import InitVar, dataclass


def default_Rs(map_side: int) -> int:
    """Sensing/planning radius by map size class (user may override)."""
    return 6 if map_side <= 20 else 8 if map_side <= 40 else 12 if map_side <= 80 else 16


@dataclass(frozen=True)
class Params:
    R_s: int = 6                  # sensing + planning radius (Chebyshev), user input
    u1: float = 1.0               # S = u1*wait + u2*travel + u3*earliness
    u2: float = 0.1
    u3: float = 0.01
    cap_util: InitVar[float | None] = None  # legacy input accepted but ignored; no utilization cap
    cap_n: int | None = None      # optional explicit SMT group-size limit; uncapped by default
    K_round: int = 20
    K_swap: int = 10              # minimum balancing budget (scaled with candidates); 0 disables
    K_unclaimed: int = 10         # local offload + auction cycles, not a global move budget
    eps: float = 1e-9
    K_T: int = 4                  # T-alignment attempts (aligned groups only)
    K_wait: int = 50              # setup broadcast/poll budget; exhaustion waits without opening the barrier
    setup_broadcast_period: int = 1
    setup_timeout_s: float = 2.0  # communication/heartbeat silence limit; does not limit movement time
    group_mode: str = "two_shot"   # "two_shot" | "one_shot" | "auto" (one-shot for groups of <= one_shot_max_workers)
    one_shot_H: int = 0           # one-shot hypercycle for unaligned groups (0 = use the instance T)
    one_shot_max_workers: int = 1
    K_refine: int = 0             # refinement proposal rounds per recharger (0 = off)
    refine_min_gain: float = 0.5  # efficiency points needed to keep a refinement
    hb_period: int = 1            # heartbeat period (time steps)
    k_missed: int = 3             # missed heartbeats before a robot is declared failed
    seed: int = 0
    rlimit: int = 0               # Z3 resource limit (deterministic); 0 = none
    retries: int = 3
    collisions: bool = True       # repo's collision constraints (ON; the repo ships them commented out)
    exclusive_recharge: bool = False
    phase2_optimize_cost: bool = False  # optional minimization of recharger primitive cost Y

    @property
    def R_c(self) -> int:
        return 2 * self.R_s
