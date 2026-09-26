#!/nix/store/v6wsd9nyglmwh32yn7w8z11dq9cksg4m-nodejs-24.20.0/bin/node
import { APP_NAME } from "./config.js";
import { configureHttpDispatcher } from "./core/http-dispatcher.js";
import { main } from "./main.js";
process.title = `${APP_NAME}-rpc`;
process.env.PI_CODING_AGENT = "true";
process.env.AI_AGENT = "pi";
process.emitWarning = (() => { });
configureHttpDispatcher();
main(["--mode", "rpc", ...process.argv.slice(2)]);
//# sourceMappingURL=rpc-entry.js.map