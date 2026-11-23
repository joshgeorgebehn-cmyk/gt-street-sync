"""Bolt pattern (PCD) landing pages for gtstreet.au.

Each page is a smart collection: product type Wheels or Rims AND variant title contains the bolt pattern. Variant titles
read like "18x8.5 / 5x114.3 / +35", so new wheels appear on the right pages as soon as they are imported, with no tags
to maintain. Copy names only well known Australian fitments and always sends buyers to a fitment check, because centre
bore, offset and brake clearance vary by model and year.

  python collections/pcd_pages.py      prints the GraphQL mutation (collectionCreate, then publish to Online Store)
"""
import json

PCDS = ["5x114.3", "5x120", "6x139.7", "5x112", "5x100", "5x108", "5x127", "5x130", "5x150", "6x114.3"]

COMMON = {
    "5x114.3": "Toyota GR Yaris, GR Corolla, Camry and RAV4, Nissan 350Z, 370Z and Skyline GT-R, Mitsubishi Lancer Evolution, "
               "Subaru WRX (2015 on), Ford Mustang and Falcon BA to FG, Hyundai i30 N, Kia Stinger and Tesla Model 3 and Y",
    "5x120": "BMW E and F series (including the 1, 3 and M models of those generations), Holden Commodore VE and VF, "
             "Honda Civic Type R, Volkswagen Transporter T5 and T6, Chevrolet Camaro and Range Rover Sport (L320)",
    "6x139.7": "Toyota HiLux, Prado and Fortuner, Ford Ranger and Everest, Mitsubishi Triton and Pajero Sport, Nissan Patrol Y62, "
               "Isuzu D-Max and MU-X and Mazda BT-50",
    "5x112": "Volkswagen Golf, GTI and Golf R (Mk5 on), Audi A3, S3 and RS3, Mercedes-Benz C-Class, BMW G series including the "
             "G20 3 Series and G80 M3, Toyota GR Supra (A90) and Skoda Octavia",
    "5x100": "Subaru WRX (to 2014) and BRZ, Toyota 86 and GR86, Volkswagen Polo and Golf Mk4 and Toyota Prius",
    "5x108": "Ford Focus including the ST and RS, Ford Mondeo, most Volvo models and Jaguar XE and XF",
    "5x127": "Jeep Wrangler JK and JL, Jeep Gladiator and Jeep Grand Cherokee WK2",
    "5x130": "Porsche 911, Cayman and Boxster (stud pattern models), Porsche Cayenne, Volkswagen Touareg and Mercedes-Benz G-Class",
    "5x150": "Toyota LandCruiser 200 and 300 Series, LandCruiser 70 Series V8 and Toyota Tundra",
    "6x114.3": "Nissan Navara D40 and NP300, Nissan Pathfinder R51, Renault Alaskan and Mercedes-Benz X-Class",
}
STUDS = {"4": "four", "5": "five", "6": "six", "8": "eight"}
OFFROAD = {"6x139.7", "5x150", "6x114.3", "5x127"}
BRANDS = {   # from the live catalogue on 23/11/2026 (wheels listed in each pattern), largest first
    "5x114.3": ["GT Form", "Black Rock", "JSR", "Lenso"], "5x120": ["GT Form", "Black Rock", "JSR", "Lenso"],
    "6x139.7": ["Black Rock", "GT Form", "LOADED", "Lenso"], "5x112": ["GT Form", "JSR", "Black Rock", "BBS"],
    "5x100": ["GT Form", "JSR", "Black Rock", "Lenso"], "5x108": ["GT Form", "JSR", "OZ Racing", "BBS"],
    "5x127": ["Black Rock", "GT Form"], "5x130": ["Black Rock", "GT Form", "OZ Racing", "BBS"],
    "5x150": ["Black Rock", "GT Form", "LOADED", "Lenso"], "6x114.3": ["Black Rock", "GT Form", "LOADED", "Lenso"],
}
BRAND_HANDLE = {"GT Form": "gt-form", "Black Rock": "black-rock", "JSR": "jsr", "Lenso": "lenso", "LOADED": "loaded",
                "BBS": "bbs", "OZ Racing": "oz-racing"}


def handle(p): return p.replace(".", "-") + "-wheels"


def page(p):
    n, mm = p.split("x")
    kind = "4x4 and ute" if p in OFFROAD else "street"
    brands = BRANDS[p]
    blinks = ", ".join(f'<a href="/collections/{BRAND_HANDLE[b]}">{b}</a>' for b in brands)
    others = " · ".join(f'<a href="/collections/{handle(o)}">{o}</a>' for o in PCDS if o != p)
    extra = ("Load rating matters as much as bolt pattern on a ute or 4x4, so we check it against your vehicle's axle "
             "loads and towing setup too. " if p in OFFROAD else "")
    html = (
        f"<p>Every wheel on this page is drilled {p}: {STUDS[n]} studs on a {mm} mm circle. It is a common {kind} fitment in "
        f"Australia, found on the {COMMON[p]}.</p>"
        f"<p>Bolt pattern is only the first check. Centre bore, offset, width and brake clearance decide whether a wheel "
        f"really fits, and they change between models and years. {extra}Add your car in My garage or "
        f'<a href="/pages/quote">send us your vehicle</a> and we confirm fitment before anything ships.</p>'
        f"<p>Brands in {p}: {blinks}. Prices are shown per wheel and as a set of four.</p>"
        f"<p>Other bolt patterns: {others}. Not sure of yours? See the "
        f'<a href="/pages/wheel-fitment-guide">wheel fitment guide</a>.</p>')
    seo_d = (f"{p} wheels from {', '.join(brands[:3])} and more. Fitment checked for your car before we ship. "
             f"Free delivery over $1,000 Australia wide.")
    return {
        "title": f"{p} Wheels", "handle": handle(p), "descriptionHtml": html, "sortOrder": "BEST_SELLING",
        "seo": {"title": f"{p} Wheels | Fitment Checked | GT Street", "description": seo_d[:160]},
        "sources": [{"source": {"title": f"Wheels drilled {p}", "targetType": "PRODUCTS", "inclusion": {"matchType": "ALL", "conditions": [
            {"productType": {"matchType": "ANY", "relation": "EQUALS", "values": ["Wheels", "Rims"]}},
            {"variantTitle": {"matchType": "ANY", "relation": "CONTAINS", "values": [p]}}]}}}],
    }


if __name__ == "__main__":
    print(json.dumps({f"c{i}": page(p) for i, p in enumerate(PCDS, 1)}, ensure_ascii=False))
