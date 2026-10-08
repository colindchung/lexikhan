from unittest.mock import Mock

import pytest
from approve_email import approve
from botocore.exceptions import ClientError


def setup_session(repository, verified=True):
    session = Mock()
    cloudformation = Mock()
    cloudformation.describe_stacks.return_value = {
        "Stacks": [
            {
                "Outputs": [
                    {"OutputKey": "UserPoolId", "OutputValue": "pool"},
                    {
                        "OutputKey": "HistoryTableName",
                        "OutputValue": repository.table_name,
                    },
                ]
            }
        ]
    }
    cognito = Mock()
    cognito.admin_get_user.return_value = {
        "UserStatus": "CONFIRMED",
        "UserAttributes": [
            {"Name": "sub", "Value": "first"},
            {"Name": "email_verified", "Value": "true"},
            {"Name": "email", "Value": "person@example.test"},
        ],
    }
    ses = Mock()
    ses.get_email_identity.return_value = {"VerifiedForSendingStatus": verified}
    session.client.side_effect = lambda name: {
        "cloudformation": cloudformation,
        "cognito-idp": cognito,
        "sesv2": ses,
        "dynamodb": repository.client,
    }[name]
    return session, cognito, ses


def test_approval_requires_verification_and_sends_nothing(repository):
    session, _, ses = setup_session(repository, False)
    with pytest.raises(ValueError):
        approve(session, "dev", "person@example.test")
    assert repository.get("USER#first", "EMAIL_ACCESS") is None
    ses.send_email.assert_not_called()


def test_email_cannot_be_claimed_by_two_accounts(repository):
    session, cognito, ses = setup_session(repository)
    approve(session, "dev", "person@example.test")
    assert repository.get("USER#first", "EMAIL_ACCESS")["approved"]
    assert repository.get("USER#first", "REMINDER") is None
    cognito.admin_get_user.return_value["UserAttributes"][0]["Value"] = "second"
    with pytest.raises(ClientError):
        approve(session, "dev", "person@example.test")
    assert repository.get("USER#second", "EMAIL_ACCESS") is None
    ses.send_email.assert_not_called()
