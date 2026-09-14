from garmin_proto_lab.messages import MESSAGE_FAMILIES, MESSAGE_NAMES, MessageDisposition


def test_every_recovered_message_id_has_an_explicit_disposition() -> None:
    assert set(MESSAGE_FAMILIES) == set(MESSAGE_NAMES)
    assert all(family.rationale for family in MESSAGE_FAMILIES.values())
    assert MESSAGE_FAMILIES[5101].disposition is MessageDisposition.IMPLEMENTED
    assert MESSAGE_FAMILIES[5035].disposition is MessageDisposition.IMPLEMENTED
    assert MESSAGE_FAMILIES[5002].disposition is MessageDisposition.IMPLEMENTED
    assert MESSAGE_FAMILIES[5110].disposition is MessageDisposition.STATIC_NO_CALLSITE
    assert MESSAGE_FAMILIES[5041].disposition is MessageDisposition.OUTSIDE_FITNESS_SCOPE
