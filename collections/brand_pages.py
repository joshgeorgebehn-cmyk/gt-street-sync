"""Wheel brand page copy (collection description, shown in the About section under the products) and search descriptions.

Facts come from the live catalogue on 23/11/2026: wheel counts, rim diameters, bolt patterns and model names. Prices are
not written into copy because the supplier sync changes them daily.

  python collections/brand_pages.py    prints JSON {handle: {descriptionHtml, seo}}
"""
import json

PCD_HANDLE = {p: p.replace(".", "-") + "-wheels" for p in
              ["5x114.3", "5x120", "6x139.7", "5x112", "5x100", "5x108", "5x127", "5x130", "5x150", "6x114.3"]}

STOCKED = ("<p>Every order is checked for PCD, offset, centre bore and load rating before it ships, and the manufacturer "
           "warranty is passed on in full. Prices are shown per wheel and as a set of four, with free delivery Australia "
           "wide on orders over $1,000.</p>")
QUOTED = ("<p>{name} wheels are quoted per order because sizes, finishes and supply change often. "
          '<a href="/pages/quote">Send us your car</a> and the size you want, and we reply with one written quote with '
          "fitment confirmed and freight included.</p>")

BRANDS = {
    "gt-form": dict(name="GT Form", quoted=False,
        intro="GT Form street wheels cover more fitments than almost any range we sell: 17 to 22 inch, drilled for 21 bolt "
              "patterns from 5x100 to 6x139.7, so the same design can suit a hot hatch, a European sedan, an American muscle "
              "car or a dual cab ute.",
        pcds=["5x114.3", "5x120", "5x112", "5x100", "5x108", "5x130", "6x139.7", "5x150", "6x114.3", "5x127"],
        seo="GT Form street wheels, 17 to 22 inch in 21 bolt patterns. Fitment checked for your car, priced as a set of four. Free delivery over $1,000."),
    "jsr": dict(name="JSR", quoted=False,
        intro="JSR builds street wheels in 16 to 19 inch, including four stud fitments (4x100, 4x108 and 4x114.3) that suit "
              "older Japanese and European cars. We run the JSR ST25 on our own GR Yaris.",
        pcds=["5x114.3", "5x120", "5x112", "5x100", "5x108"],
        seo="JSR street wheels in 16 to 19 inch, including four stud fitments. The ST25 runs on our own GR Yaris. Fitment checked, free delivery over $1,000."),
    "lenso": dict(name="Lenso", quoted=False,
        intro="Lenso is a Thai wheel maker with street designs like the D-1R and 90-Z and 4x4 designs like the Argo, Cartel "
              "and Amada beadlock, which is built for off road use. Sizes run from 17 to 20 inch.",
        pcds=["5x114.3", "5x120", "5x100", "6x139.7", "5x150", "6x114.3"],
        seo="Lenso street and 4x4 wheels, 17 to 20 inch: D-1R, 90-Z, Argo and Cartel. Fitment and load rating checked. Free delivery over $1,000."),
    "black-rock": dict(name="Black Rock", quoted=False,
        intro="Black Rock makes off road wheels for utes, 4x4s and SUVs, from 16 to 22 inch in 26 bolt patterns. On a "
              "touring or towing vehicle we check load rating against your axle loads as well as offset and tyre clearance.",
        pcds=["6x139.7", "5x150", "6x114.3", "5x127", "5x130", "5x120", "5x114.3"],
        seo="Black Rock off road wheels for utes and 4x4s, 16 to 22 inch. Load rating, offset and tyre clearance checked. Free delivery over $1,000."),
    "loaded": dict(name="LOADED", quoted=False,
        intro="LOADED 4x4 wheels are load rated to 1,675 kg per wheel, in 17 and 18 inch. Designs include the Canning, Gibb, "
              "Gunbarrel, Carson, Ellis and Stockton, in finishes from satin black to bronze and machined silver.",
        pcds=["6x139.7", "5x150", "6x114.3", "5x120"],
        seo="LOADED 4x4 wheels, load rated to 1,675 kg per wheel: Canning, Gibb, Gunbarrel and more. Fitment checked. Free delivery over $1,000."),
    "bbs": dict(name="BBS", quoted=True,
        intro="BBS is the German wheel maker behind decades of motorsport and factory fitments. We quote the current range, "
              "including the CH-R, CC-R, CI-R, FI-R, LM-R, RF, RI-A, RX-R, SR, SV, SX and XA, from 17 to 20 inch.",
        pcds=["5x112", "5x120", "5x130", "5x114.3", "5x108", "5x100"],
        seo="BBS forged and cast wheels from Germany: CH-R, CI-R, FI-R, LM-R and more. Quoted to order with fitment confirmed. Delivered Australia wide."),
    "oz-racing": dict(name="OZ Racing", quoted=True,
        intro="OZ Racing is an Italian wheel maker with a long motorsport history. We quote the Superforgiata, Ultraleggera, "
              "Leggenda, Formula HLT, Indy HLT, Alleggerita HLT, Ares and Zeus, from 15 to 19 inch.",
        pcds=["5x112", "5x120", "5x130", "5x108", "5x114.3", "5x100"],
        seo="OZ Racing wheels from Italy: Superforgiata, Ultraleggera, Leggenda and more. Quoted to order with fitment confirmed. Delivered Australia wide."),
    "rays": dict(name="RAYS", quoted=True,
        intro="RAYS is the Japanese maker of Volk Racing forged wheels, including the TE37, TE37SL, CE28N and ZE40, plus the "
              "Gram Lights 57CR and 57DR and Homura 2X7 cast ranges.",
        pcds=["5x114.3", "5x120", "5x112", "5x100", "5x108"],
        seo="RAYS Volk Racing TE37, CE28N and ZE40 forged wheels plus Gram Lights and Homura. Quoted to order with fitment confirmed. Delivered Australia wide."),
    "work": dict(name="WORK", quoted=True,
        intro="WORK is a Japanese wheel maker known for multi piece and forged designs. We quote the Emotion CR Kai, CR Kiwami "
              "and T7R, the three piece Meister S1 and the VS XX.",
        pcds=["5x114.3", "5x112", "5x100", "5x108"],
        seo="WORK wheels from Japan: Emotion CR Kai, CR Kiwami, T7R, Meister S1 3P and VS XX. Quoted to order with fitment confirmed. Delivered Australia wide."),
    "enkei": dict(name="ENKEI", quoted=True,
        intro="ENKEI is a Japanese wheel maker supplying factory and motorsport teams. We quote flow formed and forged designs "
              "including the RPF1, PF01, PF07, NT03+M, RS05RR, T6R and TY5.",
        pcds=["5x114.3", "5x120", "5x100"],
        seo="ENKEI wheels: RPF1, PF01, NT03+M, RS05RR and more, light and strong for street and track. Quoted to order with fitment confirmed."),
    "concaver": dict(name="Concaver", quoted=True,
        intro="Concaver wheels are designed around deep concave spoke profiles. The CVR1 to CVR9 range suits European and "
              "Japanese performance cars, with width and offset chosen for your car's guards and brakes.",
        pcds=[],
        seo="Concaver CVR1 to CVR9 deep concave wheels for European and Japanese cars. Quoted to order with PCD, offset and centre bore checked."),
}


def build(h, b):
    links = " · ".join(f'<a href="/collections/{PCD_HANDLE[p]}">{p}</a>' for p in b["pcds"])
    html = f"<p>{b['intro']}</p>"
    html += QUOTED.format(name=b["name"]) if b["quoted"] else STOCKED
    if links:
        html += f"<p>Shop {b['name']} by bolt pattern: {links}.</p>"
    html += ('<p>Not sure what fits? Add your car in My garage or read the '
             '<a href="/pages/wheel-fitment-guide">wheel fitment guide</a>.</p>')
    assert len(b["seo"]) <= 160, (h, len(b["seo"]))
    return {"descriptionHtml": html, "seo": {"description": b["seo"]}}


if __name__ == "__main__":
    print(json.dumps({h: build(h, b) for h, b in BRANDS.items()}, ensure_ascii=False))
