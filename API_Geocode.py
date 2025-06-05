import streamlit as st
import pandas as pd
import time
from io import BytesIO
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import os
import requests

#Clé LocationIQ
LOCATIONIQ_API_KEY = "pk.f77db56ba31235d07e1a1d045dc501de"

st.set_page_config(page_title="Géocodeur d'adresses", layout="centered")
st.title("Géocodeur d'adresses – COLAS Belgique")

uploaded_file = st.file_uploader("Importer un fichier Excel", type=[".xls", ".xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.success("Fichier chargé avec succès")

        colonnes = df.columns.tolist()
        col_adresse = st.selectbox("Colonne contenant les adresses", colonnes)
        col_entreprise = st.selectbox("Colonne contenant le nom de l’entreprise", colonnes)

        if st.button("Lancer le géocodage"):
            geolocator = Nominatim(user_agent="streamlit_geocoder_colas")


            def nettoyer_adresse(adresse):
                if isinstance(adresse, str):
                    # Supprimer les mentions inutiles
                    mots_a_supprimer = ["Internal Postal Box", "Bte", "Case postale", "(Biz)"]
                    for mot in mots_a_supprimer:
                        adresse = adresse.replace(mot, "")

                    # Nettoyage standard
                    adresse = adresse.replace("B ", "").strip()
                    if not adresse.lower().endswith("belgique"):
                        adresse += ", Belgique"
                    return adresse
                return ""

            def geocode_nominatim(adresse):
                try:
                    location = geolocator.geocode(adresse, timeout=10)
                    if location:
                        return round(location.latitude, 6), round(location.longitude, 6)
                except (GeocoderTimedOut, GeocoderUnavailable):
                    time.sleep(1)
                    return geocode_nominatim(adresse)
                except:
                    pass
                return None, None

            def geocode_locationiq(adresse):
                try:
                    url = "https://eu1.locationiq.com/v1/search"
                    params = {
                        "key": LOCATIONIQ_API_KEY,
                        "q": adresse,
                        "format": "json",
                        "limit": 1
                    }
                    response = requests.get(url, params=params)
                    data = response.json()
                    if isinstance(data, list) and len(data):
                        lat = float(data[0]['lat'])
                        lon = float(data[0]['lon'])
                        return round(lat, 6), round(lon, 6)
                except:
                    pass
                return None, None

            latitudes, longitudes, adresses_finales, sources = [], [], [], []
            progress = st.progress(0)
            total = len(df)

            for i in range(total):
                adresse_orig = str(df.at[i, col_adresse])
                entreprise = str(df.at[i, col_entreprise]).strip()
                source = "Nominatim"

                adresse_nettoyee = nettoyer_adresse(adresse_orig)
                lat, lon = geocode_nominatim(adresse_nettoyee)
                adresse_utilisee = adresse_nettoyee

                if (lat is None or lon is None) and entreprise:
                    adresse_alt = nettoyer_adresse(entreprise)
                    lat, lon = geocode_nominatim(adresse_alt)
                    if lat and lon:
                        adresse_utilisee = adresse_alt
                        source = "Fallback entreprise"

                if (lat is None or lon is None):
                    lat, lon = geocode_locationiq(adresse_utilisee)
                    if lat and lon:
                        source = "LocationIQ"

                if lat is None or lon is None:
                    source = "Échec"

                adresses_finales.append(adresse_utilisee)
                latitudes.append(lat)
                longitudes.append(lon)
                sources.append(source)

                progress.progress((i + 1) / total)
                time.sleep(1)

            df[col_adresse] = adresses_finales
            df["Latitude"] = latitudes
            df["Longitude"] = longitudes
            df["Source géocodage"] = sources

            st.success("Géocodage terminé")
            st.dataframe(df.head())

            # Affichage des échecs
            df_echec = df[df["Source géocodage"] == "Échec"]
            if not df_echec.empty:
                st.warning("Certaines adresses n'ont pas pu être géocodées :")
                st.dataframe(df_echec[[col_adresse, col_entreprise]])

            # Supprimer la colonne "Source géocodage"
            if "Source géocodage" in df.columns:
                df = df.drop(columns=["Source géocodage"])

            # Forcer le format des colonnes de date sans heure
            colonnes_date = ["Date début", "Date fin"]
            for col in colonnes_date:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime("%Y-%m-%d")

            # Nom du fichier XLSX
            original_name = os.path.splitext(uploaded_file.name)[0]
            final_filename = f"{original_name}_complété.xlsx"

            # Conversion en XLSX
            xlsx_data = df.to_xlsx(index=False).encode("utf-8")

            st.download_button(
                label="Télécharger le fichier XLSX complété",
                data=xlsx_data,
                file_name=final_filename,
                mime="text/xlsx"
            )

    except Exception as e:
        st.error(f"Erreur lors du traitement : {e}")
