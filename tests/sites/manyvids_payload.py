"""Small synthetic Relay store payloads for ManyVids parser tests."""

import json
from typing import Any


def video_spec(
    video_id: str,
    title: str | None = None,
    *,
    slug: str | None = None,
    duration: str = "PT2M13S",
    regular: str = "5.99",
    discounted: str = "0.00",
    on_sale: bool = False,
    free: bool = False,
    promo_rate: int = 0,
    thumbnail: str | None = "https://cdn.example/{video_id}.jpg",
) -> dict[str, object]:
    resolved_title = title or f"Video {video_id}"
    return {
        "video_id": video_id,
        "title": resolved_title,
        "slug": slug or resolved_title.lower().replace(" ", "-"),
        "duration": duration,
        "regular": regular,
        "discounted": discounted,
        "on_sale": on_sale,
        "free": free,
        "promo_rate": promo_rate,
        "thumbnail": thumbnail,
    }


def build_store_html(
    regular: list[dict[str, object]],
    mobile: list[dict[str, object]] | None = None,
    *,
    regular_total: int | None = None,
    mobile_total: int | None = None,
    page: int = 1,
    regular_first: int = 70,
    mobile_first: int = 14,
    creator_id: str = "1002990973",
    include_mobile_connection: bool = True,
    include_handle_connections: bool = True,
) -> str:
    mobile = mobile or []
    records: dict[str, Any] = {
        "client:root": {
            "__id": "client:root",
            "__typename": "__Root",
            f'profile(id:"{creator_id}")': {"__ref": "PROF1"},
        },
        "PROF1": {"__id": "PROF1", "__typename": "Profile", "localId": creator_id},
    }

    def add_connection(
        items: list[dict[str, object]], kind: str, first: int, total: int
    ) -> str:
        offset = f",offset:{(page - 1) * first}" if page > 1 else ""
        filt = 'filter:{"vertical":true}' if kind == "mobile" else "filter:{}"
        key = f'storeVideos({filt},first:{first}{offset},sort:"NEWEST")'
        conn_id = f"client:PROF1:{kind}"
        refs: list[str] = []
        for index, spec in enumerate(items):
            vid = str(spec["video_id"])
            edge_id = f"edge:{kind}:{index}:{vid}"
            video_key = f"video:{kind}:{index}:{vid}"
            price_key = f"price:{kind}:{index}:{vid}"
            records[edge_id] = {
                "__typename": "ProfileStoreVideoEdge",
                "node": {"__ref": video_key},
            }
            price = {
                "__typename": "Price",
                "free": spec.get("free", False),
                "onSale": spec.get("on_sale", False),
                "regular": spec.get("regular", "5.99"),
                "discountedPrice": spec.get("discounted", "0.00"),
                "promoRate": spec.get("promo_rate", 0),
            }
            records[price_key] = price
            video: dict[str, object] = {
                "__id": video_key,
                "__typename": "Video",
                "localId": vid,
                "slug": spec.get(
                    "slug", str(spec.get("title", "")).lower().replace(" ", "-")
                ),
                "title": spec.get("title", f"Video {vid}"),
                "duration": spec.get("duration", "PT2M13S"),
                "price": {"__ref": price_key},
            }
            if spec.get("thumbnail") is not None:
                image_key = f"image:{kind}:{index}:{vid}"
                records[image_key] = {
                    "__typename": "Image",
                    "url": str(spec["thumbnail"]).format(video_id=vid),
                }
                video["screenshot"] = {"__ref": image_key}
            records[video_key] = video
            refs.append(edge_id)
        records[conn_id] = {
            "__typename": "ProfileStoreVideoConnection",
            "totalCount": total,
            "totalWithoutFilters": total,
            "edges": {"__refs": refs},
            "pageInfo": {"__ref": f"pageInfo:{kind}"},
        }
        records[f"pageInfo:{kind}"] = {
            "__typename": "ForwardOnlyPageInfo",
            "hasNextPage": len(items) < total,
        }
        records["PROF1"][key] = {"__ref": conn_id}
        return key

    add_connection(
        regular,
        "regular",
        regular_first,
        regular_total if regular_total is not None else len(regular),
    )
    if include_mobile_connection:
        add_connection(
            mobile,
            "mobile",
            mobile_first,
            mobile_total if mobile_total is not None else len(mobile),
        )
    if include_handle_connections:
        records["HANDLE_EDGE"] = {
            "__typename": "ProfileStoreVideoEdge",
            "node": {"__ref": "HANDLE_VIDEO"},
        }
        records["HANDLE_VIDEO"] = {
            "__typename": "Video",
            "localId": "999999",
            "slug": "ignored-handle-video",
            "title": "Ignored Handle Video",
            "duration": "PT1M",
            "price": {"__ref": "HANDLE_PRICE"},
        }
        records["HANDLE_PRICE"] = {
            "__typename": "Price",
            "regular": "1.00",
            "discountedPrice": "0.00",
            "onSale": False,
            "free": False,
        }
        handle_key = (
            '__ProfileStoreVideoGrid_storeVideos_connection(filter:{},sort:"NEWEST")'
        )
        records["PROF1"][handle_key] = {"__ref": "HANDLE_CONNECTION"}
        records["HANDLE_CONNECTION"] = {
            "__typename": "ProfileStoreVideoConnection",
            "totalCount": 1,
            "edges": {"__refs": ["HANDLE_EDGE"]},
        }
    chunk = "6b:" + json.dumps(
        [
            "$",
            "$L67",
            None,
            {
                "isVideosStore": True,
                "url": {
                    "id": creator_id,
                    "slug": "creator",
                    "mainSection": "STORE",
                    "subSection": "VIDEOS",
                },
                "children": ["$", "$L68", None, {"recordMap": records}],
            },
        ],
        separators=(",", ":"),
    )
    return (
        "<html><head><title>Store</title></head><body><script>self.__next_f.push([1,"
        + json.dumps(chunk)
        + "])</script></body></html>"
    )
