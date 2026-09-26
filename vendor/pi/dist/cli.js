#!/nix/store/v6wsd9nyglmwh32yn7w8z11dq9cksg4m-nodejs-24.20.0/bin/node
import { setupCli } from "./cli/setup.js";
import { main } from "./main.js";
setupCli();
main(process.argv.slice(2));
//# sourceMappingURL=cli.js.map