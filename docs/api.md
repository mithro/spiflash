# Python API

```python
import spiflash

(chip,) = spiflash.lookup("ef4018")
chip.manufacturer, chip.names, chip.size
chip.supports("READ_1_1_4")
```

## Looking things up

```{eval-rst}
.. autofunction:: spiflash.lookup
.. autofunction:: spiflash.find
.. autofunction:: spiflash.flashes
.. autofunction:: spiflash.records
.. autofunction:: spiflash.jep106
.. autofunction:: spiflash.sources
.. autofunction:: spiflash.database
```

## The database

```{eval-rst}
.. autoclass:: spiflash.Database
   :members: lookup, find, by_manufacturer, jep106, link, load
```

## Chips and records

```{eval-rst}
.. autoclass:: spiflash.Flash
   :members:

.. autoclass:: spiflash.model.SupportedOperation
   :members:

.. autoclass:: spiflash.Record
   :members: manufacturer, id_hex, is_jedec, url, part_names

.. autoclass:: spiflash.model.OpcodeUse

.. autoclass:: spiflash.Manufacturer
```

## Operations

```{eval-rst}
.. automodule:: spiflash.opcodes
   :members: Operation, get, sort_key

.. autodata:: spiflash.opcodes.OPERATIONS
   :no-value:
```

## Helpers

```{eval-rst}
.. autofunction:: spiflash.parse_id
.. autofunction:: spiflash.model.part_names
.. autofunction:: spiflash.model.name_matches
.. autofunction:: spiflash.vendors.canonical
```
