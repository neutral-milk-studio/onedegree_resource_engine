// A map of search results that have coordinates. Plain Leaflet (no React wrapper), since
// this is the only map in the app -- OpenStreetMap tiles, no API key needed.
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import markerShadow from "leaflet/dist/images/marker-shadow.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import { useEffect, useRef } from "react";

// Vite serves these as hashed URLs; Leaflet's default icon paths assume a classic
// (non-bundled) static layout and break silently otherwise.
const MARKER_ICON = L.icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
});

export interface MapPoint {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
}

export function ResultsMap({ points }: { points: MapPoint[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);
    markersRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      markersRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const markers = markersRef.current;
    if (!map || !markers) return;
    markers.clearLayers();
    for (const point of points) {
      L.marker([point.latitude, point.longitude], { icon: MARKER_ICON }).bindPopup(point.name).addTo(markers);
    }
    if (points.length > 0) {
      const bounds = L.latLngBounds(points.map((point) => [point.latitude, point.longitude]));
      map.fitBounds(bounds, { maxZoom: 14, padding: [24, 24] });
    }
  }, [points]);

  if (points.length === 0) return null;

  return (
    <div
      ref={containerRef}
      role="group"
      aria-label={`Map of ${points.length} ${points.length === 1 ? "result" : "results"}`}
      className="h-64 w-full overflow-hidden rounded-xl border border-line"
    />
  );
}
