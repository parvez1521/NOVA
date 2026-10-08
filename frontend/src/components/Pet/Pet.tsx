import type { PetState } from "../../types/nova";
import { useState } from "react";
import type { MouseEvent, PointerEvent } from "react";

interface PetProps {
  state: PetState;
  onClick: () => void;
  onDoubleClick?: () => void;
  onDragStart?: () => void;
  onContextMenu?: (event: MouseEvent<HTMLButtonElement>) => void;
}

export function Pet({ state, onClick, onDoubleClick, onDragStart, onContextMenu }: PetProps) {
  const [gaze, setGaze] = useState({ x: 0, y: 0 });
  function track(event: PointerEvent<HTMLButtonElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    setGaze({ x: Math.max(-4, Math.min(4, ((event.clientX - bounds.left) / bounds.width - 0.5) * 8)), y: Math.max(-3, Math.min(3, ((event.clientY - bounds.top) / bounds.height - 0.5) * 6)) });
  }
  return (
    <button className={`pet pet--${state}`} type="button" onClick={onClick} onDoubleClick={onDoubleClick} onContextMenu={event => { event.preventDefault(); onContextMenu?.(event); }} onPointerDown={() => onDragStart?.()} onPointerMove={track} onPointerLeave={() => setGaze({ x: 0, y: 0 })} aria-label={`NOVA is ${state}. Open companion panel`}>
      <span className="pet__aura" aria-hidden="true" />
      <span className="pet__spark pet__spark--one" aria-hidden="true" />
      <span className="pet__spark pet__spark--two" aria-hidden="true" />
      <svg className="pet__face" viewBox="0 0 220 220" role="img" aria-label="NOVA pet">
        <defs>
          <linearGradient id="nova-face" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0" stopColor="#9a8cff" />
            <stop offset="0.55" stopColor="#5d63dd" />
            <stop offset="1" stopColor="#373a9f" />
          </linearGradient>
          <linearGradient id="nova-ear" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#bd9cff" />
            <stop offset="1" stopColor="#6268dd" />
          </linearGradient>
          <linearGradient id="nova-body" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0" stopColor="#7d83e9" />
            <stop offset="0.6" stopColor="#454bb1" />
            <stop offset="1" stopColor="#262a78" />
          </linearGradient>
          <linearGradient id="nova-visor" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#d5ffff" stopOpacity=".95" />
            <stop offset="1" stopColor="#6bd9ee" stopOpacity=".18" />
          </linearGradient>
          <filter id="nova-shadow" x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="9" floodColor="#2c276a" floodOpacity="0.4" stdDeviation="8" />
          </filter>
        </defs>
        <path className="pet__body" d="M70 157c0-15 18-25 40-25s40 10 40 25v42c-12 12-28 17-40 17s-28-5-40-17Z" fill="url(#nova-body)" filter="url(#nova-shadow)" />
        <path className="pet__shoulder pet__shoulder--left" d="M69 164c-15 2-22 10-20 24 2 8 9 11 18 8Z" fill="#5d64d2" />
        <path className="pet__shoulder pet__shoulder--right" d="m151 164c15 2 22 10 20 24-2 8-9 11-18 8Z" fill="#5d64d2" />
        <rect className="pet__chest" x="88" y="165" width="44" height="27" rx="10" fill="#20265f" stroke="#8bf0f3" strokeOpacity=".42" />
        <circle className="pet__chest-light" cx="110" cy="178" r="5" fill="#93f2f3" />
        <path className="pet__antenna" d="M110 37V21" fill="none" stroke="#8cf0f2" strokeLinecap="round" strokeWidth="4" />
        <circle className="pet__antenna-light" cx="110" cy="16" r="7" fill="#a9ffff" />
        <path className="pet__ear" d="M48 92c-16-2-24-11-22-23 2-9 10-14 21-11l20 10Z" fill="url(#nova-ear)" />
        <path className="pet__ear" d="m172 92c16-2 24-11 22-23-2-9-10-14-21-11l-20 10Z" fill="url(#nova-ear)" />
        <path className="pet__head" d="M110 38c-46 0-79 29-79 77 0 47 31 76 79 76s79-29 79-76c0-48-33-77-79-77Z" fill="url(#nova-face)" filter="url(#nova-shadow)" />
        <path className="pet__shine" d="M63 69c12-14 28-21 44-23" fill="none" stroke="#d8d6ff" strokeLinecap="round" strokeOpacity=".5" strokeWidth="7" />
        <path className="pet__visor" d="M57 86c17-19 89-19 106 0v45c-17 18-89 18-106 0Z" fill="url(#nova-visor)" opacity=".25" />
        <g className="pet__eyes" fill="#fff">
          <ellipse cx="78" cy="104" rx="13" ry="17" />
          <ellipse cx="142" cy="104" rx="13" ry="17" />
          <g style={{ transform: `translate(${gaze.x}px, ${gaze.y}px)` }}>
            <circle cx="82" cy="101" r="5" fill="#29285f" />
            <circle cx="146" cy="101" r="5" fill="#29285f" />
            <circle cx="84" cy="99" r="1.7" fill="#fff" />
            <circle cx="148" cy="99" r="1.7" fill="#fff" />
          </g>
        </g>
        <path className="pet__mouth" d="M101 137c6 8 13 8 19 0" fill="none" stroke="#29285f" strokeLinecap="round" strokeWidth="5" />
        <circle className="pet__cheek" cx="58" cy="128" r="8" fill="#ff9fca" opacity=".7" />
        <circle className="pet__cheek" cx="162" cy="128" r="8" fill="#ff9fca" opacity=".7" />
      </svg>
    </button>
  );
}
