import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ApiError, IS_SNAPSHOT, type MeResponse, me } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { Rule } from "@/components/Brand";
import { StaticNotice } from "@/components/StaticNotice";
import { perRequest } from "@/lib/rendering";

/**
 * The session, as the API sees it.
 *
 * Everything here comes from `/v1/auth/me` rather than from anything this side
 * decided. Entitlement is the API's to answer (ADR-0006), and a UI that kept
 * its own idea of who you are is a UI that eventually shows a steward a queue
 * the server will refuse to give them.
 */

export async function generateMetadata() {
  const t = await getTranslations("account");
  return { title: t("title") };
}

export default async function AccountPage() {
  const t = await getTranslations("account");

  return (
    <div className="max-w-2xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <Rule />
      </header>
      {IS_SNAPSHOT ? <StaticNotice /> : <Session />}
    </div>
  );
}

async function Session() {
  await perRequest();
  const t = await getTranslations("account");

  let caller: MeResponse;
  try {
    caller = await me();
  } catch (error) {
    // "The API did not answer" is not "you are signed out". This used to
    // fabricate `authenticated: false` and render "You are not signed in" with
    // a Sign in button, so a reader with a perfectly good session was told they
    // had none and sent into a flow that was failing for the same reason. The
    // page's own docstring says everything here comes from `/v1/auth/me` rather
    // than from anything this side decided; an invented answer is this side
    // deciding.
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <EmptyState title={t("unavailable")}>
        <p>{t("unavailableHelp", { status: status || "no response" })}</p>
      </EmptyState>
    );
  }

  if (!caller.authenticated) {
    return (
      <EmptyState title={t("signedOut")}>
        <p>
          <Link href="/signin" className="og-cta">
            {t("signIn")}
          </Link>
        </p>
      </EmptyState>
    );
  }

  return (
    <div className="space-y-6">
      <dl className="og-card divide-y p-5 text-sm" style={{ borderColor: "var(--border)" }}>
        <Row label={t("email")} value={caller.email ?? t("notCaptured")} />
        <Row label={t("role")} value={caller.role ?? t("notCaptured")} />
        <Row
          label={t("steward")}
          value={caller.is_steward ? t("yes") : t("no")}
          note={caller.is_steward ? undefined : t("stewardHelp")}
        />
        <Row
          label={t("custodian")}
          value={
            caller.custodian_of.length > 0
              ? t("custodianCount", { count: caller.custodian_of.length })
              : t("no")
          }
          note={t("custodianHelp")}
        />
      </dl>

      {caller.is_steward ? (
        <p>
          <Link href="/admin/review" className="og-cta">
            {t("openQueue")}
          </Link>
        </p>
      ) : null}

      <p className="text-sm text-[color:var(--muted)]">{t("tokensHelp")}</p>
    </div>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-2.5 first:pt-0 last:pb-0">
      <dt className="og-eyebrow">{label}</dt>
      <dd className="min-w-0 text-right">
        <span className="font-medium">{value}</span>
        {note ? (
          <span className="mt-0.5 block text-xs text-[color:var(--muted)]">{note}</span>
        ) : null}
      </dd>
    </div>
  );
}
