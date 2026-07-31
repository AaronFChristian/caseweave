"""Synthetic entity population.

Deliberately imperfect: a fraction of parties share an address or device, and
a fraction are duplicate records for the same human with a mangled name. Clean
synthetic data would make entity resolution look trivial, which would be a lie.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from caseweave import config as cfg
from caseweave.models import Account, Address, KycRisk, Party, PartyType

FIRST = [
    "James",
    "Maria",
    "Wei",
    "Aisha",
    "Daniel",
    "Sofia",
    "Omar",
    "Priya",
    "Lucas",
    "Fatima",
    "Hiroshi",
    "Elena",
    "Kwame",
    "Ingrid",
    "Rafael",
    "Nadia",
    "Tomas",
    "Yuki",
    "Amara",
    "Viktor",
    "Leila",
    "Diego",
    "Anya",
    "Samuel",
    "Mei",
    "Idris",
    "Clara",
    "Jonas",
    "Rosa",
    "Ade",
]
LAST = [
    "Okafor",
    "Nguyen",
    "Silva",
    "Haddad",
    "Kowalski",
    "Ferreira",
    "Ivanov",
    "Chen",
    "Rossi",
    "Dubois",
    "Andersson",
    "Mbeki",
    "Tanaka",
    "Reyes",
    "Novak",
    "Fischer",
    "Karim",
    "Petrov",
    "Costa",
    "Larsen",
    "Mensah",
    "Bergman",
    "Vargas",
    "Sato",
    "Adeyemi",
    "Moretti",
    "Halvorsen",
]
BIZ_HEAD = [
    "Harborline",
    "Cedar Point",
    "Vantage",
    "Northgate",
    "Blue Meridian",
    "Ironwood",
    "Solstice",
    "Kestrel",
    "Fairmount",
    "Redwater",
    "Pinnacle",
    "Bright Harbor",
    "Old Mill",
    "Silverbrook",
    "Copperfield",
]
BIZ_TAIL = [
    "Trading LLC",
    "Logistics Inc",
    "Imports Ltd",
    "Consulting Group",
    "Holdings LLC",
    "Restaurant Group",
    "Auto Sales",
    "Construction Co",
    "Marine Services",
    "Textiles Ltd",
]
MERCHANTS = [
    "Greenline Grocers",
    "Volt Utilities",
    "Metro Transit",
    "Cinepoint",
    "Northside Pharmacy",
    "FuelUp Station",
    "Cloudstream Media",
    "Rent Portal",
    "Apex Insurance",
    "Campus Bookstore",
    "Riverside Gym",
    "TeleConnect",
]
CITIES = [
    ("San Diego", "CA", "92101"),
    ("Austin", "TX", "78701"),
    ("Newark", "NJ", "07102"),
    ("Tampa", "FL", "33602"),
    ("Columbus", "OH", "43215"),
    ("Fresno", "CA", "93721"),
]
INDUSTRIES = [
    "restaurant",
    "import_export",
    "construction",
    "logistics",
    "retail",
    "professional_services",
    "used_vehicles",
    "money_services",
]
CASH_INTENSIVE = {"restaurant", "used_vehicles", "money_services", "retail"}


def _mangle(name: str, rng: random.Random) -> str:
    """Produce a realistic near-duplicate spelling of a name."""
    parts = name.split()
    mode = rng.choice(["initial", "swap", "hyphen", "drop_vowel"])
    if mode == "initial" and len(parts) >= 2:
        return f"{parts[0][0]}. {parts[-1]}"
    if mode == "swap" and len(parts) >= 2:
        return f"{parts[-1]}, {parts[0]}"
    if mode == "hyphen" and len(parts) >= 2:
        return f"{parts[0]}-{parts[-1]}"
    head = parts[0]
    if len(head) > 3:
        head = head[0] + head[1:].replace("a", "", 1).replace("e", "", 1)
    return f"{head} {parts[-1]}"


class Population:
    """Container for the generated entity graph."""

    def __init__(self) -> None:
        self.addresses: list[Address] = []
        self.parties: list[Party] = []
        self.accounts: list[Account] = []
        self.duplicate_pairs: list[tuple[str, str]] = []

    def accounts_for(self, party_id: str) -> list[Account]:
        return [a for a in self.accounts if a.party_id == party_id]

    def by_type(self, t: PartyType) -> list[Party]:
        return [p for p in self.parties if p.party_type == t]


def build_population(rng: random.Random, today: date) -> Population:
    pop = Population()

    n_addr = int((cfg.N_INDIVIDUALS + cfg.N_BUSINESSES) * 0.85)
    for i in range(n_addr):
        city, region, pc = rng.choice(CITIES)
        pop.addresses.append(
            Address(
                address_id=f"AD{i:04d}",
                line1=f"{rng.randint(10, 9899)} {rng.choice(['Elm', 'Pine', 'Grand', ' 4th', 'Harbor', 'Mission'])} St",
                city=city,
                region=region,
                postcode=pc,
                country=cfg.DOMESTIC_COUNTRY,
            )
        )

    device_pool = [f"DV{i:04d}" for i in range(int(cfg.N_INDIVIDUALS * 0.9))]
    pid = 0

    def _new_party(**kw) -> Party:
        nonlocal pid
        p = Party(party_id=f"P{pid:04d}", canonical_id=f"P{pid:04d}", **kw)
        pid += 1
        pop.parties.append(p)
        return p

    # ---- individuals -----------------------------------------------------
    for _ in range(cfg.N_INDIVIDUALS):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        country = (
            rng.choice(sorted(cfg.HIGH_RISK_COUNTRIES))
            if rng.random() < 0.04
            else cfg.DOMESTIC_COUNTRY
        )
        risk = (
            KycRisk.HIGH
            if country != cfg.DOMESTIC_COUNTRY
            else (KycRisk.MEDIUM if rng.random() < 0.18 else KycRisk.LOW)
        )
        _new_party(
            name=f"{fn} {ln}",
            first_name=fn,
            last_name=ln,
            dob=today - timedelta(days=rng.randint(21 * 365, 70 * 365)),
            party_type=PartyType.INDIVIDUAL,
            country=country,
            address_id=rng.choice(pop.addresses).address_id,
            device_id=rng.choice(device_pool),
            email=f"{fn.lower()}.{ln.lower()}{rng.randint(1, 99)}@mail.example",
            phone=f"+1858{rng.randint(1000000, 9999999)}",
            kyc_risk=risk,
            onboarded_at=today - timedelta(days=rng.randint(120, 3000)),
        )

    # ---- businesses ------------------------------------------------------
    for _ in range(cfg.N_BUSINESSES):
        industry = rng.choice(INDUSTRIES)
        _new_party(
            name=f"{rng.choice(BIZ_HEAD)} {rng.choice(BIZ_TAIL)}",
            party_type=PartyType.BUSINESS,
            country=cfg.DOMESTIC_COUNTRY,
            address_id=rng.choice(pop.addresses).address_id,
            device_id=None,
            email=None,
            phone=None,
            kyc_risk=KycRisk.MEDIUM if industry in CASH_INTENSIVE else KycRisk.LOW,
            industry=industry,
            onboarded_at=today - timedelta(days=rng.randint(200, 4000)),
        )

    # ---- merchants (counterparties for background spend) -----------------
    merch_addr = pop.addresses[0].address_id
    for i in range(cfg.N_MERCHANTS):
        base = MERCHANTS[i % len(MERCHANTS)]
        _new_party(
            name=base if i < len(MERCHANTS) else f"{base} #{i}",
            party_type=PartyType.MERCHANT,
            country=cfg.DOMESTIC_COUNTRY,
            address_id=merch_addr,
            device_id=None,
            email=None,
            phone=None,
            kyc_risk=KycRisk.LOW,
            industry="retail",
            onboarded_at=today - timedelta(days=rng.randint(400, 4000)),
        )

    # ---- duplicate identities -------------------------------------------
    individuals = [p for p in pop.parties if p.party_type == PartyType.INDIVIDUAL]
    for src in rng.sample(individuals, cfg.N_DUPLICATE_IDENTITIES):
        dup = _new_party(
            name=_mangle(src.name, rng),
            first_name=src.first_name,
            last_name=src.last_name,
            dob=src.dob,
            party_type=PartyType.INDIVIDUAL,
            country=src.country,
            address_id=src.address_id,
            device_id=src.device_id,
            email=None,
            phone=src.phone,
            kyc_risk=src.kyc_risk,
            onboarded_at=src.onboarded_at + timedelta(days=rng.randint(30, 600)),
        )
        pop.duplicate_pairs.append((src.party_id, dup.party_id))

    # ---- accounts --------------------------------------------------------
    aid = 0
    for p in pop.parties:
        n = 1 if p.party_type == PartyType.MERCHANT else rng.choice([1, 1, 2])
        for k in range(n):
            atype = (
                "business"
                if p.party_type in (PartyType.BUSINESS, PartyType.MERCHANT)
                else ("savings" if k == 1 else "checking")
            )
            pop.accounts.append(
                Account(
                    account_id=f"A{aid:05d}",
                    party_id=p.party_id,
                    account_type=atype,
                    opened_at=p.onboarded_at,
                )
            )
            aid += 1

    return pop
