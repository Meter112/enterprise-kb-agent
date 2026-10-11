"""手写 BM25 与 rank_bm25 库版对照类单测（不碰 Qdrant/模型）。"""
from kb_agent.ingest.sparse_bm25 import BM25Encoder, RankBM25Baseline


def test_fit_and_encode_shapes() -> None:
    enc = BM25Encoder()
    enc.fit(["云盘支持文件同步和分享", "员工手册规定考勤打卡规则", "公开API使用Bearer鉴权"])
    idx, val = enc.encode("云盘同步规则", for_query=True)
    assert len(idx) == len(val) > 0
    # L2 归一化：模长为 1（与 Qdrant 稀疏向量 Cosine 语义一致）
    assert abs(sum(s * s for s in val) - 1.0) < 1e-6


def test_encode_drops_out_of_vocab_tokens() -> None:
    enc = BM25Encoder()
    enc.fit(["年假制度"])
    idx, val = enc.encode("报销制度流程", for_query=True)
    # "报销/流程" 不在词表，只剩 "制度"
    assert [enc.vocab.get("制度")] == idx
    assert len(val) == 1


def test_query_side_uses_search_mode_segmentation() -> None:
    enc = BM25Encoder()
    enc.fit(["中华人民共和国公司法"])
    # cut_for_search 会切出子词（如 "中华/华人"），索引侧 cut 不会——查询侧 token 集更细
    idx, _ = enc.encode("公司法", for_query=True)
    assert idx, "查询侧应命中词表"


def test_rankbm25_baseline_ranks_by_score() -> None:
    texts = [
        "年假天数按累计工龄确定，满一年不满三年为五天",
        "绩效奖金按季度考核结果发放",
        "年假申请需要提前三个工作日在系统提交",
    ]
    bm25 = RankBM25Baseline(texts)
    ranked = bm25.search("年假怎么申请", top_k=2)
    assert [i for i, _ in ranked] == [2, 0]  # 含"年假+申请"的文档排最前
    assert ranked[0][1] >= ranked[1][1]


def test_rankbm25_baseline_empty_query() -> None:
    bm25 = RankBM25Baseline(["测试文档"])
    assert bm25.search("", top_k=3) == []
