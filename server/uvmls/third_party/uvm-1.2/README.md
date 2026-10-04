# Accellera UVM 1.2 (bundled copy)

`src/`, `LICENSE.txt` and `NOTICE.txt` are copied unmodified from the official Accellera UVM 1.2 release kit:

- URL: https://www.accellera.org/images/downloads/standards/uvm/uvm-1.2.tar.gz
- SHA-256 of the tarball: `502a2e605ce552bfd9767803c7e99a053715b00f7a9c4c511c3fbfddfb30157c`
- Licence: Apache License 2.0 (`LICENSE.txt`), with the attribution notices in `NOTICE.txt`

The kit's `docs/`, `examples/` and `bin/` are left out. So are three Vim swap files that the tarball happens to contain under `src/` (`src/base/.nfs*`, `src/seq/.nfs*`); they are editor leftovers, not source code.

The language server indexes this copy when no other UVM library is configured or part of the project.

To check it against the original kit:

```bash
curl -LO https://www.accellera.org/images/downloads/standards/uvm/uvm-1.2.tar.gz
sha256sum uvm-1.2.tar.gz
tar xzf uvm-1.2.tar.gz
diff -r -x '.nfs*' uvm-1.2/src src && cmp uvm-1.2/LICENSE.txt LICENSE.txt && cmp uvm-1.2/NOTICE.txt NOTICE.txt
```
