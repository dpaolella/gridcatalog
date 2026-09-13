"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { catalogReturn } from "@/lib/navigation";

export function CatalogReturnLink() {
  const params = useSearchParams();
  const t = useTranslations("empty");
  return (
    <Link href={catalogReturn(params.get("returnTo"))} className="hover:text-[color:var(--foreground)]">
      ← {t("backToSearch")}
    </Link>
  );
}
