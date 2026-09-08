"use client";

import { useEffect, useRef, useState } from "react";
import Script from "next/script";

/**
 * The intake form's human-verification challenge (PRD §F3, #40).
 *
 * **Renders nothing unless a site key is configured**, which is the default
 * and the state this repository ships in. A key belongs to a deployment, not
 * to a codebase, so the component's no-key path is the one most people will
 * see and it has to be a clean absence rather than a broken widget.
 *
 * The token goes to the API as `captcha_token`. The server treats an
 * unconfigured deployment and an unreachable provider as passes and only an
 * active rejection as a failure — see `services/api/captcha.py` for why an
 * outage must not become an intake outage.
 */
const SITE_KEY = process.env.NEXT_PUBLIC_CAPTCHA_SITE_KEY ?? "";

export function isChallengeEnabled(): boolean {
  return Boolean(SITE_KEY);
}

export function Challenge({ onToken }: { onToken: (token: string | null) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!SITE_KEY || !ready || !container.current) return;
    const turnstile = (window as unknown as { turnstile?: TurnstileApi }).turnstile;
    if (!turnstile) return;
    // Cleared before rendering: React can run this effect twice in
    // development's strict mode, and a second widget in the same node renders
    // two challenges where the reader expects one.
    container.current.innerHTML = "";
    turnstile.render(container.current, {
      sitekey: SITE_KEY,
      callback: (token: string) => onToken(token),
      "expired-callback": () => onToken(null),
      "error-callback": () => onToken(null),
    });
  }, [ready, onToken]);

  if (!SITE_KEY) return null;

  return (
    <>
      <Script
        src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
        onLoad={() => setReady(true)}
      />
      <div ref={container} />
    </>
  );
}

type TurnstileApi = {
  render: (
    element: HTMLElement,
    options: {
      sitekey: string;
      callback: (token: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ) => void;
};
