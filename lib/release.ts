/**
 * Release identity of this Web build. The version must equal package.json;
 * the source commit is stamped by build/release-stamp-vite-plugin.ts and reads
 * "development" outside a production build.
 */
export const WEB_RELEASE_VERSION = "1.1.1";

const STAMPED_SOURCE_COMMIT: string = "__DEEPSHOVEL_SOURCE_COMMIT__";

export const WEB_SOURCE_COMMIT = STAMPED_SOURCE_COMMIT.startsWith("__") ? "development" : STAMPED_SOURCE_COMMIT;

/** For example `1.1.0+0123456789ab`; shown by /api/health for live acceptance. */
export const WEB_RELEASE = `${WEB_RELEASE_VERSION}+${WEB_SOURCE_COMMIT}`;
