"""Model-specific content-address qualification; no numeric recall authority."""
import math

POLICY={"identity":"reranker-qualified-band-v1","minimum_similarity":0.6,"maximum_distance_from_best":0.03,"fixed_top_n":False}
# Values are the existing Qwen3 0.6B Q8 embedding manifest's qualification policy.
MODEL_SHA256="06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"
RERANKER_SHA256="22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48"
EMBEDDING_POLICY={"identity":"embedding-qualified-reranker-v1","minimum_similarity":.25,"maximum_distance_from_best":.5,"fixed_top_n":False}
VERSION="desktop-pet-local-qwen-exchange-address-v2"

def select_scores(scores,policy=POLICY):
    if not isinstance(scores,dict) or any(not isinstance(k,str) or not k for k in scores):raise ValueError("invalid exact-ID scores")
    if any(type(v) not in (int,float) or not math.isfinite(v) or not -1<=v<=1 for v in scores.values()):raise ValueError("invalid cosine")
    if policy.get("fixed_top_n") is not False:raise ValueError("content address cannot use fixed Top-N")
    floor=policy["minimum_similarity"];band=policy["maximum_distance_from_best"]
    if not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in (floor,band)):raise ValueError("invalid model policy")
    if not scores:return []
    cutoff=max(floor,max(scores.values())-band)
    return sorted((k for k,v in scores.items() if v>=cutoff),key=lambda k:(-scores[k],k))
