import streamlit as st
import pandas as pd
import time
from io import BytesIO
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import os
import requests
import re

# ───────────────────────────────────────────────────────────────────────────────
# Config / constantes
# ───────────────────────────────────────────────────────────────────────────────
LOCATIONIQ_API_KEY = "pk.f77db56ba31235d07e1a1d045dc501de"
CENTROIDS = {"BE": (50.64, 4.67)}  # centroïde BE pour filtrer
PAYS_DEFAUT = "BE"  # Belgique only

st.set_page_config(page_title="Géocodeur d'adresses", layout="centered")
st.title("Géocodeur d'adresses – COLAS Belgique")

# Géocodeur global (évite de le recréer à chaque appel)
geolocator = Nominatim(user_agent="streamlit_geocoder_colas")

# ───────────────────────────────────────────────────────────────────────────────
# Normalisation / parsing d’adresse (Belgique)
# ───────────────────────────────────────────────────────────────────────────────
ABBR = [
    (r"\bchem\.?\b", "chemin"),
    (r"\bav\.?\b", "avenue"),
    (r"\bbd\b", "boulevard"),
    (r"\brte\b", "route"),
]

def normaliser_voie(s: str) -> str:
    s0 = (s or "").strip()
    s_low = s0.lower()
    for pat, rep in ABBR:
        s_low = re.sub(pat, rep, s_low)
    s_low = s_low.replace("serres castet", "Serres-Castet")
    s_low = re.sub(r"\s+", " ", s_low).strip()
    # BE : conserver l’apostrophe sur Grand'Route
    return s_low.replace("grand'route", "Grand'Route")

def nettoyer_adresse(adresse: str) -> str:
    """
    Belgique only : renvoie l’adresse NORMALISÉE SANS ajouter ', Belgique'.
    Retire un éventuel pays déjà présent (on passe 'be' à l’API).
    """
    if not isinstance(adresse, str):
        return ""
    s = adresse
    for mot in ["Internal Postal Box", "Bte", "Case postale", "(Biz)"]:
        s = s.replace(mot, "")
    s = s.replace("B ", " ").strip()
    s = re.sub(r",?\s*(belgique|belgium)\s*$", "", s, flags=re.I).strip()
    return normaliser_voie(s)

def parse_be_address(adresse: str):
    """
    Forme courante : 'Grand'Route 71, 4367 Crisnée'
    Retourne: (streetnum, street, housenumber, postcode, city) ou None
    """
    if not isinstance(adresse, str):
        return None
    s = adresse.strip()
    m = re.match(r"^(?P<streetnum>.+?)\s*,\s*(?P<cp>\d{4})\s+(?P<city>[^,]+)$", s)
    if not m:
        return None
    streetnum = m.group("streetnum").strip()
    postcode = m.group("cp").strip()
    city = m.group("city").strip()

    m2 = re.match(r"^(?P<street>.+?)\s+(?P<num>\d+[A-Za-z\-]?)$", streetnum)
    if not m2:
        return (streetnum, streetnum, None, postcode, city)
    street = m2.group("street").strip()
    housenumber = m2.group("num").strip()
    return (streetnum, street, housenumber, postcode, city)

def est_centroid(lat: float, lon: float, cc="BE") -> bool:
    c = CENTROIDS.get(cc)
    return bool(c) and abs(lat - c[0]) < 0.5 and abs(lon - c[1]) < 0.5

# ───────────────────────────────────────────────────────────────────────────────
# Géocodage
# ───────────────────────────────────────────────────────────────────────────────
def geocode_locationiq(adresse_sans_pays: str):
    """
    Belgique only – essaie en requête STRUCTURÉE (street/postcode/city), sinon q=
    Valide : type adresse/bâtiment et non-centroïde.
    """
    try:
        url = "https://eu1.locationiq.com/v1/search"
        params = {
            "key": LOCATIONIQ_API_KEY,
            "format": "json",
            "limit": 1,
            "accept-language": "fr",
            "countrycodes": "be",
            "normalizecity": 1,
        }

        parsed = parse_be_address(adresse_sans_pays)
        if parsed:
            streetnum, street, housenumber, postcode, city = parsed
            if housenumber:
                params.update({
                    "street": f"{street} {housenumber}",
                    "city": city,
                    "postcode": postcode,
                })
            else:
                params["q"] = f"{streetnum}, {postcode} {city}"
        else:
            params["q"] = adresse_sans_pays

        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            hit = data[0]
            lat = float(hit["lat"]); lon = float(hit["lon"])
            typ = hit.get("type", "")
            place_rank = hit.get("place_rank", 0)

            # N’accepter que des résultats fins (adresse/bâtiment) et pas centroïde
            if typ in {"house", "building", "address"} and (not place_rank or place_rank >= 28):
                if not est_centroid(lat, lon, "BE"):
                    return round(lat, 6), round(lon, 6)
        return None, None
    except Exception:
        return None, None

def geocode_nominatim(adresse_sans_pays: str, retries=2):
    try:
        location = geolocator.geocode(
            adresse_sans_pays,
            addressdetails=True,
            country_codes="be",
            timeout=15,
            language="fr",
        )
        if location:
            lat, lon = float(location.latitude), float(location.longitude)
            if not est_centroid(lat, lon, "BE"):
                return round(lat, 6), round(lon, 6)
    except (GeocoderTimedOut, GeocoderUnavailable):
        if retries > 0:
            time.sleep(1)
            return geocode_nominatim(adresse_sans_pays, retries-1)
    except Exception as e:
        st.write(f"Erreur Nominatim: {e}")
    return None, None

@st.cache_data(show_spinner=False)
def geocode_cache(adresse_sans_pays: str):
    # 1/ LocationIQ (structuré si possible)
    lat, lon = geocode_locationiq(adresse_sans_pays)
    source = "LocationIQ"
    if lat is not None and lon is not None:
        return lat, lon, source

    # 2/ Variante simple : sans code postal (débloque certains cas)
    variante = re.sub(r"\b\d{4}\b", "", adresse_sans_pays).strip()
    if variante != adresse_sans_pays:
        lat, lon = geocode_locationiq(variante)
        if lat is not None and lon is not None:
            return lat, lon, "LocationIQ (variante)"

    # 3/ Nominatim en fallback
    lat, lon = geocode_nominatim(adresse_sans_pays)
    source = "Nominatim" if lat and lon else "Échec"
    return lat, lon, source

# ───────────────────────────────────────────────────────────────────────────────
# UI
# ───────────────────────────────────────────────────────────────────────────────
uploaded_file = st.file_uploader("Importer un fichier Excel", type=[".xls", ".xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file, dtype=str)
        st.success("Fichier chargé avec succès")

        colonnes = df.columns.tolist()
        col_adresse = st.selectbox("Colonne contenant les adresses", colonnes)
        col_entreprise = st.selectbox("Colonne contenant le nom de l’entreprise", colonnes)

        if st.button("Lancer le géocodage"):
            latitudes, longitudes, adresses_finales, sources = [], [], [], []
            progress = st.progress(0)
            total = len(df)

            for i in range(total):
                adresse_orig = str(df.at[i, col_adresse])
                entreprise = str(df.at[i, col_entreprise]).strip()
                st.write(f"Géocodage en cours ({i+1}/{total}) : {adresse_orig}")

                # Nettoie l’adresse et RETIRE le pays (on force BE via l’API)
                adresse_sans_pays = nettoyer_adresse(adresse_orig)
                lat, lon, source = geocode_cache(adresse_sans_pays)

                # Fallback entreprise si échec
                if (lat is None or lon is None) and entreprise:
                    entreprise_sans_pays = nettoyer_adresse(entreprise)
                    lat, lon, source = geocode_cache(entreprise_sans_pays)
                    if lat and lon:
                        adresse_sans_pays = entreprise_sans_pays
                        source = f"Fallback entreprise ({source})"

                # Adresse affichée (ajout visuel du pays)
                adresse_finale_affichee = f"{adresse_sans_pays}, Belgique"
                adresses_finales.append(adresse_finale_affichee)
                latitudes.append(lat)
                longitudes.append(lon)
                sources.append(source)

                progress.progress((i + 1) / total)

            # Résultats
            df[col_adresse] = adresses_finales
            df["Latitude"] = latitudes
            df["Longitude"] = longitudes
            df["Source géocodage"] = sources

            st.success("✅ Géocodage terminé")
            st.dataframe(df.head())

            # Affichage des échecs
            df_echec = df[df["Source géocodage"] == "Échec"]
            if not df_echec.empty:
                st.warning("Certaines adresses n'ont pas pu être géocodées :")
                st.dataframe(df_echec[[col_adresse, col_entreprise]])

            # Nettoyage colonne source avant export (si tu veux)
            df_export = df.drop(columns=["Source géocodage"], errors="ignore")

            # Formatage dates éventuelles
            colonnes_date = ["Date début", "Date fin"]
            for col in colonnes_date:
                if col in df_export.columns:
                    df_export[col] = pd.to_datetime(df_export[col], errors='coerce').dt.strftime("%Y-%m-%d")

            # Export XLSX
            original_name = os.path.splitext(uploaded_file.name)[0]
            final_filename = f"{original_name}_complété.xlsx"
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_export.to_excel(writer, index=False)
            output.seek(0)

            st.download_button(
                label="📥 Télécharger le fichier XLSX complété",
                data=output,
                file_name=final_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except Exception as e:
        st.error(f"Erreur lors du traitement : {e}")
