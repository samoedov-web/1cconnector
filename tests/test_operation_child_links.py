from connector.models import ExpectedPayment, Match, Transaction


def test_operation_child_links_are_nullable_foreign_keys():
    for model in (Transaction, Match, ExpectedPayment):
        column = model.__table__.c.operation_id

        assert column.nullable is True
        assert {
            foreign_key.target_fullname
            for foreign_key in column.foreign_keys
        } == {"operations.id"}
